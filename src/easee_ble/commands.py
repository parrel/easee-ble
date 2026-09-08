"""The JSON requests a charger accepts, and the command/argument ids."""

from __future__ import annotations

import enum
import json


class Command(enum.IntEnum):
    """Confirmed COMMAND-channel command ids (all take a single argument, id 1)."""

    # Restart the charger. Sent as a bare {"Id": 1}, so probing ids without arguments reboots it.
    RESTART = 1
    # Sent by the app immediately after the handshake; appears to open the session.
    OPEN_SESSION = 32
    # Ask the charger to scan for WiFi networks; answered with res.networks in the ack.
    SCAN_WIFI = 7
    # WiFi credentials: argument 1 the SSID, 2 the passphrase. Two empty strings detach.
    SET_WIFI = 53
    # List the enrolled user tokens. Sent as bare {"Id": 102}; answer is JSON in Comment.
    LIST_USER_TOKENS = 102
    # Look up one key by slot and name; answered with {"un","ut","enabled","exists"} in Comment.
    GET_USER_TOKEN = 101
    # Enrol or update a key in a slot.
    SET_USER_TOKEN = 98
    # How the Bluetooth radio behaves; value is a BtEnableMode. Writes Config field 20.
    SET_BT_ENABLE_MODE = 96
    # Lock or unlock the charging cable in the socket; value is a boolean.
    SET_CABLE_LOCKED = 30
    # Switch the charger on or off - Easee's isEnabled, the app's main toggle.
    SET_CHARGER_ENABLED = 29
    # Deprecated alias; the name was wrong, this is not about access control.
    SET_ACCESS_CONTROL = 29
    # Charging-phase mode; value is a PhaseMode. Not acted on until the charger restarts.
    SET_PHASE_MODE = 38
    # LED strip brightness, 0-100.
    SET_LED_BRIGHTNESS = 40
    # Circuit/charger maximum current, in whole amperes.
    SET_MAX_CHARGER_CURRENT = 47
    # Dynamic (temporary) charger current, in whole amperes.
    SET_DYNAMIC_CHARGER_CURRENT = 48
    # Circuit current limit per phase: one argument or three. Writes Config fields 3/4/5.
    SET_CIRCUIT_MAX_CURRENT = 50
    # Offline per-phase current limit, same shape. Writes Config fields 8/9/10.
    SET_OFFLINE_MAX_CIRCUIT_CURRENT = 24


class BtEnableMode(enum.IntEnum):
    """Values for :attr:`Command.SET_BT_ENABLE_MODE`, read off Config field 20."""

    # Advertise only after a 5-second press of the charger's button.
    BUTTON_PRESS = 1
    # Advertise continuously.
    ALWAYS_ON = 3


class NetworkStatus(enum.IntEnum):
    """Values of ``res.nws``, present in *every* Command acknowledgement."""

    # The join failed - a wrong passphrase, in the capture that named it.
    FAILED = 0
    CONNECTING = 1
    DISCONNECTED = 2
    CONNECTED = 3
    # Deliberately unnamed: a transient late step of a successful connection.
    # Once identified, the real name goes first and this stays below it as an alias.
    UNKNOWN_4 = 4


def network_status(code: int | None) -> NetworkStatus | None:
    """The :class:`NetworkStatus` for a code, or ``None``; ``NetworkStatus()`` raises."""
    if code is None:
        return None
    try:
        return NetworkStatus(int(code))
    except ValueError:
        return None


class PhaseMode(enum.IntEnum):
    """Values for :attr:`Command.SET_PHASE_MODE` (matches the ``phaseMode`` field)."""

    LOCKED_1_PHASE = 1
    AUTO = 2
    LOCKED_3_PHASE = 3


# The two constants the app sends with :attr:`Command.OPEN_SESSION`.
_OPEN_SESSION_ARGS = (46, 2)

# Argument id for the single value of every single-valued command.
_ARG = 1
# Sanity bound on a current in amperes.
_MAX_AMPS = 40


def _dumps(obj: object) -> bytes:
    """Compact JSON, matching how the app serialises requests (no whitespace)."""
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def read_request(uid: int, serial: str) -> bytes:
    """A read request for the STATE or CONFIG channel."""
    return _dumps({"uid": uid, "sn": serial})


def _value(value: object) -> str:
    """Stringify a value the way the charger expects: booleans lowercase."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_request(command_id: int, arguments: list[tuple[int, object]]) -> bytes:
    """A write request for the COMMAND channel."""
    return _dumps(
        {
            "Id": int(command_id),
            "Arguments": [{"Id": int(aid), "Value": _value(value)} for aid, value in arguments],
        }
    )


def _single(command: Command, value: object) -> bytes:
    return write_request(command, [(_ARG, value)])


def set_led_brightness(percent: int) -> bytes:
    """Set the LED strip brightness (0-100)."""
    if not 0 <= percent <= 100:
        raise ValueError("brightness must be between 0 and 100")
    return _single(Command.SET_LED_BRIGHTNESS, percent)


def set_max_charger_current(amperes: int) -> bytes:
    """Set the charger's maximum current, in whole amperes."""
    if not 0 <= amperes <= _MAX_AMPS:
        raise ValueError(f"current must be between 0 and {_MAX_AMPS} A")
    return _single(Command.SET_MAX_CHARGER_CURRENT, amperes)


def set_dynamic_charger_current(amperes: int) -> bytes:
    """Set the dynamic (temporary) charger current, in whole amperes."""
    if not 0 <= amperes <= _MAX_AMPS:
        raise ValueError(f"current must be between 0 and {_MAX_AMPS} A")
    return _single(Command.SET_DYNAMIC_CHARGER_CURRENT, amperes)


def set_phase_mode(mode: PhaseMode) -> bytes:
    """Set the charging-phase mode."""
    return _single(Command.SET_PHASE_MODE, int(PhaseMode(mode)))


def set_cable_locked(locked: bool) -> bytes:
    """Lock or unlock the charging cable in the socket."""
    return _single(Command.SET_CABLE_LOCKED, bool(locked))


def token_request(
    command_id: int, *, slot: int | None = None, name: str | None = None, token: str | None = None
) -> bytes:
    """A user-token request: parameters at the top level, no ``Arguments``."""
    request: dict[str, object] = {"Id": int(command_id)}
    if slot is not None:
        request["us"] = str(slot)
    if name is not None:
        request["un"] = name
    if token is not None:
        request["ut"] = token
    return _dumps(request)


def list_user_tokens() -> bytes:
    """List the enrolled RFID / account keys."""
    return token_request(Command.LIST_USER_TOKENS)


def get_user_token(slot: int, name: str) -> bytes:
    """Look up one key by slot and name."""
    return token_request(Command.GET_USER_TOKEN, slot=slot, name=name)


def set_user_token(slot: int, name: str, token: str) -> bytes:
    """Enrol or update a key in a slot."""
    return token_request(Command.SET_USER_TOKEN, slot=slot, name=name, token=token)


def restart() -> bytes:
    """Restart the charger."""
    return _dumps({"Id": int(Command.RESTART)})


def scan_wifi(limit: int = 10) -> bytes:
    """Ask the charger to scan for WiFi networks."""
    return _single(Command.SCAN_WIFI, int(limit))


def set_wifi(ssid: str, passphrase: str) -> bytes:
    """Set the charger's WiFi credentials, or detach it from WiFi."""
    return write_request(Command.SET_WIFI, [(1, ssid), (2, passphrase)])


def open_session() -> bytes:
    """The command the app sends first on every connection."""
    return write_request(
        Command.OPEN_SESSION, [(1, _OPEN_SESSION_ARGS[0]), (2, _OPEN_SESSION_ARGS[1])]
    )


def set_bt_enable_mode(mode: BtEnableMode) -> bytes:
    """Set how the charger's Bluetooth radio behaves."""
    return _single(Command.SET_BT_ENABLE_MODE, int(BtEnableMode(mode)))


def set_charger_enabled(enabled: bool) -> bytes:
    """Switch the charger on or off - Easee's ``isEnabled``."""
    return _single(Command.SET_CHARGER_ENABLED, bool(enabled))


def set_access_control(enabled: bool) -> bytes:
    """Deprecated misnomer for :func:`set_charger_enabled`."""
    return set_charger_enabled(enabled)


def _three_phase(command: Command, p1: int, p2: int | None, p3: int | None) -> bytes:
    """One argument for all three phases, or three arguments, one per phase."""
    for amps in (p1, p2, p3):
        if amps is not None and not 0 <= amps <= _MAX_AMPS:
            raise ValueError(f"current must be between 0 and {_MAX_AMPS} A")
    if p2 is None and p3 is None:
        return _single(command, p1)
    if p2 is None or p3 is None:
        raise ValueError("give either one current for all phases, or all three")
    return write_request(command, [(1, p1), (2, p2), (3, p3)])


def set_circuit_max_current(p1: int, p2: int | None = None, p3: int | None = None) -> bytes:
    """Set the circuit's current limit, in whole amperes."""
    return _three_phase(Command.SET_CIRCUIT_MAX_CURRENT, p1, p2, p3)


def set_offline_max_circuit_current(p1: int, p2: int | None = None, p3: int | None = None) -> bytes:
    """Set the per-phase limit used while the charger is offline."""
    return _three_phase(Command.SET_OFFLINE_MAX_CIRCUIT_CURRENT, p1, p2, p3)
