"""A sans-io Easee BLE session: no sockets, no bleak - just bytes in and out."""

from __future__ import annotations

import itertools
import json
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
    # The COMMAND id, which the charger echoes as the reply's "id"; None on other channels.
    command_id: int | None = None


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
        command_id = json.loads(plaintext)["Id"] if channel is Channel.COMMAND else None
        return Request(channel, frame.hex().encode("ascii"), command_id)

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

    # -- actions

    def reboot(self) -> Request:
        """Restart the charger."""
        return self._encrypted(Channel.COMMAND, commands.reboot())

    def play_lights(self) -> Request:
        """Run the LED animation the app plays on every connection."""
        return self._encrypted(Channel.COMMAND, commands.play_lights())

    def run_self_test(self) -> Request:
        """Run the charger's self test."""
        return self._encrypted(Channel.COMMAND, commands.run_self_test())

    def factory_reset(self) -> Request:
        """Reset the charger to factory settings."""
        return self._encrypted(Channel.COMMAND, commands.factory_reset())

    # -- access and RFID keys

    def authorize_charging(self, token: str) -> Request:
        """Authorise charging with a key token."""
        return self._encrypted(Channel.COMMAND, commands.authorize_charging(token))

    def deauthorize_charging(self, token: str) -> Request:
        """Withdraw a key token's authorisation to charge."""
        return self._encrypted(Channel.COMMAND, commands.deauthorize_charging(token))

    def set_rfid_pairing_mode(self, timeout: int = 60) -> Request:
        """Wait ``timeout`` seconds for a tag to be scanned."""
        return self._encrypted(Channel.COMMAND, commands.set_rfid_pairing_mode(timeout))

    def set_local_authorization(self, required: bool) -> Request:
        """Require a key before charging starts."""
        return self._encrypted(Channel.COMMAND, commands.set_local_authorization(required))

    def list_local_rfids(self) -> Request:
        """List the names of the keys enrolled on the charger."""
        return self._encrypted(Channel.COMMAND, commands.list_local_rfids())

    def get_local_rfid(self, name: str) -> Request:
        """Look up one enrolled key by name."""
        return self._encrypted(Channel.COMMAND, commands.get_local_rfid(name))

    def add_local_rfid(self, name: str, token: str) -> Request:
        """Enrol a key under a name."""
        return self._encrypted(Channel.COMMAND, commands.add_local_rfid(name, token))

    def remove_local_rfid(self, token: str) -> Request:
        """Remove the key with this token."""
        return self._encrypted(Channel.COMMAND, commands.remove_local_rfid(token))

    def clear_local_rfids(self) -> Request:
        """Remove every enrolled key."""
        return self._encrypted(Channel.COMMAND, commands.clear_local_rfids())

    # -- currents

    def set_max_charger_current(self, amperes: int) -> Request:
        """Set the charger's maximum current, in whole amperes."""
        return self._encrypted(Channel.COMMAND, commands.set_max_charger_current(amperes))

    def set_dynamic_charger_current(self, amperes: int) -> Request:
        """Set the dynamic (temporary) charger current, in whole amperes."""
        return self._encrypted(Channel.COMMAND, commands.set_dynamic_charger_current(amperes))

    def pause_charging(self) -> Request:
        """Pause charging by setting the dynamic current to 0 A."""
        return self._encrypted(Channel.COMMAND, commands.pause_charging())

    def resume_charging(self, amperes: int) -> Request:
        """Resume charging by restoring a dynamic current."""
        return self._encrypted(Channel.COMMAND, commands.resume_charging(amperes))

    def set_circuit_rated_current(
        self, p1: int, p2: int | None = None, p3: int | None = None
    ) -> Request:
        """Set the circuit's rated current (the fuse): one value for all phases, or three."""
        return self._encrypted(Channel.COMMAND, commands.set_circuit_rated_current(p1, p2, p3))

    def set_max_circuit_current(
        self, p1: int, p2: int | None = None, p3: int | None = None
    ) -> Request:
        """Set the circuit's maximum current: one value for all phases, or three."""
        return self._encrypted(Channel.COMMAND, commands.set_max_circuit_current(p1, p2, p3))

    def set_dynamic_circuit_current(
        self, p1: int, p2: int | None = None, p3: int | None = None
    ) -> Request:
        """Set the circuit's dynamic current: one value for all phases, or three."""
        return self._encrypted(Channel.COMMAND, commands.set_dynamic_circuit_current(p1, p2, p3))

    def set_fallback_circuit_current(
        self, p1: int, p2: int | None = None, p3: int | None = None
    ) -> Request:
        """Set the circuit's fallback current: one value for all phases, or three."""
        return self._encrypted(Channel.COMMAND, commands.set_fallback_circuit_current(p1, p2, p3))

    def set_idle_current(self, enabled: bool) -> Request:
        """Keep a trickle of current flowing to a parked car."""
        return self._encrypted(Channel.COMMAND, commands.set_idle_current(enabled))

    # -- settings

    def set_charger_enabled(self, enabled: bool) -> Request:
        """Switch the charger on or off (Easee's ``isEnabled``, Config field 1)."""
        return self._encrypted(Channel.COMMAND, commands.set_charger_enabled(enabled))

    def set_phase_mode(self, mode: commands.PhaseMode) -> Request:
        """Set the charging-phase mode (see :class:`easee_ble.commands.PhaseMode`)."""
        return self._encrypted(Channel.COMMAND, commands.set_phase_mode(mode))

    def set_led_brightness(self, percent: int) -> Request:
        """Set the LED strip brightness, 0-100."""
        return self._encrypted(Channel.COMMAND, commands.set_led_brightness(percent))

    def set_cable_locked(self, locked: bool) -> Request:
        """Lock or unlock the charging cable in the socket."""
        return self._encrypted(Channel.COMMAND, commands.set_cable_locked(locked))

    def set_bt_enable_mode(self, mode: commands.BtEnableMode) -> Request:
        """Set how the charger's Bluetooth radio behaves."""
        return self._encrypted(Channel.COMMAND, commands.set_bt_enable_mode(mode))

    # -- wifi

    def scan_wifi(self, limit: int = 10) -> Request:
        """Scan for WiFi networks; the reply lists them."""
        return self._encrypted(Channel.COMMAND, commands.scan_wifi(limit))

    def set_wifi(self, ssid: str, passphrase: str) -> Request:
        """Set the WiFi credentials, or clear them with two empty strings."""
        return self._encrypted(Channel.COMMAND, commands.set_wifi(ssid, passphrase))

    # -- MID meter

    def get_mid_public_key(self) -> Request:
        """Read the MID meter's public key."""
        return self._encrypted(Channel.COMMAND, commands.get_mid_public_key())

    def display_mid_public_key(self) -> Request:
        """Show the MID meter's public key on the charger's display."""
        return self._encrypted(Channel.COMMAND, commands.display_mid_public_key())

    # -- replies --------------------------------------------------------------

    def parse(self, channel: Channel, data: bytes) -> Frame | dict[str, Any]:
        """Parse a reply: a Frame on the protobuf channels, a dict on COMMAND."""
        if channel in _PROTOBUF_CHANNELS:
            return parse_response(data)
        if channel is Channel.COMMAND:
            return parse_command_response(data)
        raise SessionError(f"no reply parser for {channel.name}")

    # TIME is a true GATT read, not a request/notify channel, so it builds no Request.
