"""A sans-io Easee BLE session: no sockets, no bleak - just bytes in and out."""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import commands, crypto
from .frames import Frame, parse_command_response, parse_response
from .jpake import ECJPake
from .protocol import Channel

# Channels whose replies are plaintext protobuf State/Config frames.
_PROTOBUF_CHANNELS = frozenset({Channel.STATE, Channel.CONFIG, Channel.STRUCTURE, Channel.DEBUG})


from .exceptions import SessionError  # noqa: E402  (re-exported for callers)


@dataclass(frozen=True)
class Request:
    """Bytes to write to a characteristic."""

    channel: Channel
    data: bytes


class Session:
    """One authenticated conversation with a charger."""

    def __init__(
        self,
        pin: str | bytes,
        serial: str,
        *,
        rng: Callable[[int], bytes] = os.urandom,
    ) -> None:
        self.serial = serial
        secret = pin.encode() if isinstance(pin, str) else pin
        self._jpake = ECJPake(secret, rng=rng)
        self._rng = rng
        self.session_key: bytes | None = None
        self._uid = itertools.count(1)

    # -- handshake ------------------------------------------------------------

    def start_handshake(self) -> Request:
        """First message: EC-JPAKE round one, written raw to HELLO."""
        return Request(Channel.HELLO, self._jpake.write_round_one())

    def read_round_one(self, reply: bytes) -> Request:
        """Consume the round-one reply and return the round-two message."""
        self._jpake.read_round_one(reply)
        return Request(Channel.HELLO, self._jpake.write_round_two())

    def read_round_two(self, reply: bytes) -> None:
        """Consume the round-two reply and derive the session key."""
        self._jpake.read_round_two(reply)
        self.session_key = self._jpake.derive_secret()

    @property
    def established(self) -> bool:
        """Whether the session key has been derived."""
        return self.session_key is not None

    # -- requests -------------------------------------------------------------

    def _encrypted(self, channel: Channel, plaintext: bytes) -> Request:
        if self.session_key is None:
            raise SessionError("handshake not complete")
        frame = crypto.encrypt(self.session_key, plaintext, rng=self._rng)
        return Request(channel, frame.hex().encode("ascii"))

    def poll_state(self, uid: int | None = None) -> Request:
        """Request a State frame (electrical readings, dynamic current, ...)."""
        pt = commands.read_request(uid if uid is not None else next(self._uid), self.serial)
        return self._encrypted(Channel.STATE, pt)

    def poll_config(self, uid: int | None = None) -> Request:
        """Request a Config frame (max current, phase mode, LED, site, ...)."""
        pt = commands.read_request(uid if uid is not None else next(self._uid), self.serial)
        return self._encrypted(Channel.CONFIG, pt)

    def poll_structure(self, uid: int | None = None) -> Request:
        """Request a Structure frame."""
        pt = commands.read_request(uid if uid is not None else next(self._uid), self.serial)
        return self._encrypted(Channel.STRUCTURE, pt)

    def poll_debug(self, uid: int | None = None) -> Request:
        """Request a Debug frame (``bt.get_debug``). Contents unconfirmed."""
        pt = commands.read_request(uid if uid is not None else next(self._uid), self.serial)
        return self._encrypted(Channel.DEBUG, pt)

    def command(self, command_id: int, arguments: list[tuple[int, object]]) -> Request:
        """Issue an arbitrary COMMAND-channel write (see :mod:`easee_ble.commands`)."""
        return self._encrypted(Channel.COMMAND, commands.write_request(command_id, arguments))

    def set_led_brightness(self, percent: int) -> Request:
        """Set the LED strip brightness, 0-100."""
        return self._encrypted(Channel.COMMAND, commands.set_led_brightness(percent))

    def set_max_charger_current(self, amperes: int) -> Request:
        """Set the charger's maximum current, in whole amperes."""
        return self._encrypted(Channel.COMMAND, commands.set_max_charger_current(amperes))

    def set_dynamic_charger_current(self, amperes: int) -> Request:
        """Set the dynamic (temporary) charger current, in whole amperes."""
        return self._encrypted(Channel.COMMAND, commands.set_dynamic_charger_current(amperes))

    def set_phase_mode(self, mode: commands.PhaseMode) -> Request:
        """Set the charging-phase mode (see :class:`easee_ble.commands.PhaseMode`)."""
        return self._encrypted(Channel.COMMAND, commands.set_phase_mode(mode))

    def set_charger_enabled(self, enabled: bool) -> Request:
        """Switch the charger on or off (Easee's ``isEnabled``, Config field 1)."""
        return self._encrypted(Channel.COMMAND, commands.set_charger_enabled(enabled))

    def set_access_control(self, enabled: bool) -> Request:
        """Deprecated misnomer for :meth:`set_charger_enabled`."""
        return self.set_charger_enabled(enabled)

    def set_bt_enable_mode(self, mode: commands.BtEnableMode) -> Request:
        """Set how the charger's Bluetooth radio behaves."""
        return self._encrypted(Channel.COMMAND, commands.set_bt_enable_mode(mode))

    def set_cable_locked(self, locked: bool) -> Request:
        """Lock or unlock the charging cable in the socket."""
        return self._encrypted(Channel.COMMAND, commands.set_cable_locked(locked))

    def open_session(self) -> Request:
        """Send the command the official app sends first on every connection."""
        return self._encrypted(Channel.COMMAND, commands.open_session())

    def list_user_tokens(self) -> Request:
        """List enrolled RFID / account keys."""
        return self._encrypted(Channel.COMMAND, commands.list_user_tokens())

    def get_user_token(self, slot: int, name: str) -> Request:
        """Look up one enrolled key by slot and name."""
        return self._encrypted(Channel.COMMAND, commands.get_user_token(slot, name))

    def set_user_token(self, slot: int, name: str, token: str) -> Request:
        """Enrol or update a key in a slot."""
        return self._encrypted(Channel.COMMAND, commands.set_user_token(slot, name, token))

    def set_circuit_max_current(
        self, p1: int, p2: int | None = None, p3: int | None = None
    ) -> Request:
        """Set the circuit current limit: one value for all phases, or three."""
        return self._encrypted(Channel.COMMAND, commands.set_circuit_max_current(p1, p2, p3))

    def set_offline_max_circuit_current(
        self, p1: int, p2: int | None = None, p3: int | None = None
    ) -> Request:
        """Set the per-phase limit used while the charger is offline."""
        return self._encrypted(
            Channel.COMMAND, commands.set_offline_max_circuit_current(p1, p2, p3)
        )

    # -- replies --------------------------------------------------------------

    def parse(self, channel: Channel, data: bytes) -> Frame | dict[str, Any]:
        """Parse a reply: a Frame on the protobuf channels, a dict on COMMAND."""
        if channel in _PROTOBUF_CHANNELS:
            return parse_response(data)
        if channel is Channel.COMMAND:
            return parse_command_response(data)
        raise SessionError(f"no reply parser for {channel.name}")

    # TIME is a true GATT read, not a request/notify channel, so it builds no Request.
