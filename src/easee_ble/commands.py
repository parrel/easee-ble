"""The JSON requests a charger accepts, and the command/argument ids."""

from __future__ import annotations

import enum
import json


class Command(enum.IntEnum):
    """COMMAND-channel ids; the untested ones say so above their function."""

    REBOOT = 1
    SCAN_WIFI = 7
    RUN_SELF_TEST = 16
    SET_FALLBACK_CIRCUIT_CURRENT = 21
    SET_DYNAMIC_CIRCUIT_CURRENT = 22
    SET_MAX_CIRCUIT_CURRENT = 24
    AUTHORIZE_CHARGING = 25
    DEAUTHORIZE_CHARGING = 26
    SET_ENABLED = 29
    SET_LOCK_CABLE_PERMANENTLY = 30
    PLAY_LIGHTS = 32
    SET_LOCAL_AUTHORIZATION = 33
    SET_IDLE_CURRENT = 34
    SET_PHASE_MODE = 38
    SET_LED_BRIGHTNESS = 40
    SET_MAX_CHARGER_CURRENT = 47
    # Pausing and resuming are writes of the dynamic charger current.
    SET_DYNAMIC_CHARGER_CURRENT = 48
    # Two empty strings clear the WiFi credentials.
    SET_CIRCUIT_RATED_CURRENT = 50
    SET_WIFI = 53
    SET_RFID_PAIRING_MODE = 69
    SET_BT_ENABLE_MODE = 96
    FACTORY_RESET = 97
    ADD_LOCAL_RFID = 98
    REMOVE_LOCAL_RFID = 99
    CLEAR_LOCAL_RFIDS = 100
    GET_LOCAL_RFID = 101
    GET_LOCAL_RFIDS = 102
    GET_MID_PUBLIC_KEY = 110
    DISPLAY_MID_PUBLIC_KEY = 115


# Commands the charger answers only when done; a self test took minutes.
SLOW_COMMANDS: frozenset[Command] = frozenset({Command.RUN_SELF_TEST})


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


# The app sends LED mode 46 (BLUETOOTH_CONNECTED) and a constant 2 on every connection.
_PLAY_LIGHTS_ARGS = (46, 2)
# Argument id for the single value of every single-valued command.
_ARG = 1
# The app sends "us" as "1" in every key request; it is not a slot.
_KEY_US = "1"
# The app's second argument when authorising and deauthorising; its meaning is unknown.
_AUTHORIZE_CODE = "42"
_DEAUTHORIZE_CODE = "0"
# Sanity bound on a current in amperes.
_MAX_AMPS = 40


def _dumps(obj: object) -> bytes:
    """Compact JSON, matching how the app serialises requests (no whitespace)."""
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def _value(value: object) -> str:
    """Stringify a value the way the charger expects: booleans lowercase."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def read_request(uid: int, serial: str) -> bytes:
    """A read request for the STATE or CONFIG channel."""
    return _dumps({"uid": uid, "sn": serial})


def write_request(command_id: int, arguments: list[tuple[int, object]]) -> bytes:
    """A write request for the COMMAND channel."""
    return _dumps(
        {
            "Id": int(command_id),
            "Arguments": [{"Id": int(aid), "Value": _value(value)} for aid, value in arguments],
        }
    )


def _key_request(command: Command, **fields: str) -> bytes:
    """An RFID-key request: parameters at the top level, no ``Arguments``."""
    return _dumps({"Id": int(command), **fields})


def _bare(command: Command) -> bytes:
    """A request the charger runs on the id alone."""
    return _dumps({"Id": int(command)})


def _single(command: Command, value: object) -> bytes:
    return write_request(command, [(_ARG, value)])


def _amps(command: Command, amperes: int) -> bytes:
    if not 0 <= amperes <= _MAX_AMPS:
        raise ValueError(f"current must be between 0 and {_MAX_AMPS} A")
    return _single(command, amperes)


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


# -- actions ------------------------------------------------------------------


def reboot() -> bytes:
    """Restart the charger; it acknowledges before the link drops."""
    return _bare(Command.REBOOT)


def play_lights() -> bytes:
    """Run the LED animation the app plays on every connection."""
    return write_request(
        Command.PLAY_LIGHTS, [(1, _PLAY_LIGHTS_ARGS[0]), (2, _PLAY_LIGHTS_ARGS[1])]
    )


def run_self_test() -> bytes:
    """Run the charger's self test."""
    return _bare(Command.RUN_SELF_TEST)


# Untested, irreversible
def factory_reset() -> bytes:
    """Reset the charger to factory settings."""
    return _bare(Command.FACTORY_RESET)


# -- access and RFID keys -----------------------------------------------------


# The app's own shape, untested on a charger: the key token, then a constant "42".
def authorize_charging(token: str) -> bytes:
    """Authorise charging with a key token: an enrolled tag's, or ``userid_<account id>``."""
    return write_request(Command.AUTHORIZE_CHARGING, [(1, token), (2, _AUTHORIZE_CODE)])


# The app's own shape, untested on a charger: the key token, then "0".
def deauthorize_charging(token: str) -> bytes:
    """Withdraw a key token's authorisation to charge."""
    return write_request(Command.DEAUTHORIZE_CHARGING, [(1, token), (2, _DEAUTHORIZE_CODE)])


# The scanned tag goes to the Easee cloud as an account key, not to the charger's list.
def set_rfid_pairing_mode(timeout: int = 60) -> bytes:
    """Wait ``timeout`` seconds for a tag to be scanned; ``ledMode`` reads 29 meanwhile."""
    return _single(Command.SET_RFID_PAIRING_MODE, int(timeout))


def set_local_authorization(required: bool) -> bytes:
    """Require a key before charging starts; the app calls this private access."""
    return _single(Command.SET_LOCAL_AUTHORIZATION, bool(required))


def list_local_rfids() -> bytes:
    """List the names of the keys enrolled on the charger."""
    return _key_request(Command.GET_LOCAL_RFIDS)


def get_local_rfid(name: str) -> bytes:
    """Look up one enrolled key by name."""
    return _key_request(Command.GET_LOCAL_RFID, us=_KEY_US, un=name)


def add_local_rfid(name: str, token: str) -> bytes:
    """Enrol a key under a name; the token is the tag's UID in hex."""
    return _key_request(Command.ADD_LOCAL_RFID, us=_KEY_US, un=name, ut=token)


def remove_local_rfid(token: str) -> bytes:
    """Remove the key with this token."""
    return _key_request(Command.REMOVE_LOCAL_RFID, ut=token)


def clear_local_rfids() -> bytes:
    """Remove every enrolled key, the app's own account key included."""
    return _key_request(Command.CLEAR_LOCAL_RFIDS)


# -- currents -----------------------------------------------------------------


def set_max_charger_current(amperes: int) -> bytes:
    """Set the charger's maximum current, in whole amperes."""
    return _amps(Command.SET_MAX_CHARGER_CURRENT, amperes)


def set_dynamic_charger_current(amperes: int) -> bytes:
    """Set the dynamic (temporary) charger current, in whole amperes."""
    return _amps(Command.SET_DYNAMIC_CHARGER_CURRENT, amperes)


def pause_charging() -> bytes:
    """Pause charging, which the app does by setting the dynamic current to 0 A."""
    return set_dynamic_charger_current(0)


def resume_charging(amperes: int) -> bytes:
    """Resume charging by restoring a dynamic current."""
    if amperes <= 0:
        raise ValueError("resuming needs a current above 0 A; use pause_charging() to stop")
    return set_dynamic_charger_current(amperes)


# No field reads the fuse back; lowering it caps the fallback current in Config 3/4/5.
def set_circuit_rated_current(p1: int, p2: int | None = None, p3: int | None = None) -> bytes:
    """Set the circuit's rated current (the fuse), in whole amperes."""
    return _three_phase(Command.SET_CIRCUIT_RATED_CURRENT, p1, p2, p3)


def set_max_circuit_current(p1: int, p2: int | None = None, p3: int | None = None) -> bytes:
    """Set the circuit's maximum current, in whole amperes."""
    return _three_phase(Command.SET_MAX_CIRCUIT_CURRENT, p1, p2, p3)


def set_dynamic_circuit_current(p1: int, p2: int | None = None, p3: int | None = None) -> bytes:
    """Set the circuit's dynamic (temporary) current, in whole amperes; a reboot clears it."""
    return _three_phase(Command.SET_DYNAMIC_CIRCUIT_CURRENT, p1, p2, p3)


def set_fallback_circuit_current(p1: int, p2: int | None = None, p3: int | None = None) -> bytes:
    """Set the circuit's current without a network connection, in whole amperes."""
    return _three_phase(Command.SET_FALLBACK_CIRCUIT_CURRENT, p1, p2, p3)


def set_idle_current(enabled: bool) -> bytes:
    """Keep a trickle of current flowing to a parked car."""
    return _single(Command.SET_IDLE_CURRENT, bool(enabled))


# -- settings -----------------------------------------------------------------


def set_charger_enabled(enabled: bool) -> bytes:
    """Switch the charger on or off - Easee's ``isEnabled``."""
    return _single(Command.SET_ENABLED, bool(enabled))


def set_phase_mode(mode: PhaseMode) -> bytes:
    """Set the charging-phase mode."""
    return _single(Command.SET_PHASE_MODE, int(PhaseMode(mode)))


def set_led_brightness(percent: int) -> bytes:
    """Set the LED strip brightness (0-100)."""
    if not 0 <= percent <= 100:
        raise ValueError("brightness must be between 0 and 100")
    return _single(Command.SET_LED_BRIGHTNESS, percent)


def set_cable_locked(locked: bool) -> bytes:
    """Lock or unlock the charging cable in the socket."""
    return _single(Command.SET_LOCK_CABLE_PERMANENTLY, bool(locked))


def set_bt_enable_mode(mode: BtEnableMode) -> bytes:
    """Set how the charger's Bluetooth radio behaves."""
    return _single(Command.SET_BT_ENABLE_MODE, int(BtEnableMode(mode)))


# -- wifi ---------------------------------------------------------------------


def scan_wifi(limit: int = 10) -> bytes:
    """Scan for WiFi networks; read them from the reply with ``frames.wifi_networks``."""
    return _single(Command.SCAN_WIFI, int(limit))


def set_wifi(ssid: str, passphrase: str) -> bytes:
    """Set the charger's WiFi credentials, or clear them with two empty strings."""
    return write_request(Command.SET_WIFI, [(1, ssid), (2, passphrase)])


# -- MID meter ----------------------------------------------------------------


def get_mid_public_key() -> bytes:
    """Read the MID meter's public key, as hex DER in the reply's comment."""
    return _bare(Command.GET_MID_PUBLIC_KEY)


def display_mid_public_key() -> bytes:
    """Show the MID meter's public key on the charger's display."""
    return _single(Command.DISPLAY_MID_PUBLIC_KEY, True)
