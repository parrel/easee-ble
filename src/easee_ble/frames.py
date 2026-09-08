"""Decoding the frames a charger sends back."""

from __future__ import annotations

import enum
import json
import struct
import warnings
from dataclasses import dataclass, field
from typing import Any

HEADER_LEN = 6


class MessageType(enum.Enum):
    """Header byte 1, one per answering channel."""

    CONFIG = 0x40
    STATE = 0x41
    STRUCTURE = 0x42
    DEBUG = 0x43


class ChargerOpMode(enum.IntEnum):
    """State field 12. Confirmed by watching it move from idle to charging."""

    OFFLINE = 0
    DISCONNECTED = 1
    AWAITING_START = 2  # car connected, paused / waiting to start
    CHARGING = 3
    COMPLETED = 4
    ERROR = 5
    READY_TO_CHARGE = 6
    # Seen on an idle charger with no car attached, and confirmed by the app
    AWAITING_AUTHORIZATION = 7
    # Not observed locally; carried over from the cloud table for completeness.
    DE_AUTHORIZING = 8


def charger_op_mode(code: int | None) -> ChargerOpMode | None:
    """The :class:`ChargerOpMode` for a code, or ``None``; ``ChargerOpMode()`` raises."""
    if code is None:
        return None
    try:
        return ChargerOpMode(int(code))
    except ValueError:
        return None


# State field 5, reasonForNoCurrent: why the charger is not delivering current.
REASON_FOR_NO_CURRENT: dict[int, str] = {
    0: "OK, charging or ready to charge",
    1: "Max circuit limit too low",
    2: "Max dynamic circuit limit too low",
    3: "Max dynamic offline limit too low",
    4: "Circuit fuse too low",
    5: "Waiting in queue",
    6: "Waiting in fully charged queue",
    7: "Illegal grid type",
    8: "No current request received",
    9: "Not connected to master",
    10: "Equalizer current too low",
    11: "Phase not connected",
    25: "Limited by circuit fuse",
    26: "Limited by circuit max limit",
    27: "Limited by circuit dynamic limit",
    28: "Limited by equalizer",
    29: "Limited by load balancing",
    30: "Limited by offline settings",
    50: "Secondary unit not requesting current, or no car connected",
    51: "Max charger limit too low",
    52: "Max dynamic charger limit too low",
    53: "Charger disabled",
    54: "Waiting for schedule or authorisation",
    55: "Pending authorisation",
    56: "Charger in error state",
    57: "Erratic EV",
    75: "Limited by cable rating",
    76: "Limited by schedule",
    77: "Limited by charger max limit",
    78: "Limited by charger dynamic limit",
    79: "EV is not charging",
    80: "Limited by local adjustment",
    81: "Limited by EV",
    100: "Undefined",
}


# Stable identifiers for the codes above; the descriptions will be reworded, these will not.
REASON_FOR_NO_CURRENT_SLUGS: dict[int, str] = {
    0: "ok",
    1: "max_circuit_limit_too_low",
    2: "max_dynamic_circuit_limit_too_low",
    3: "max_dynamic_offline_limit_too_low",
    4: "circuit_fuse_too_low",
    5: "waiting_in_queue",
    6: "waiting_in_fully_charged_queue",
    7: "illegal_grid_type",
    8: "no_current_request_received",
    9: "not_connected_to_master",
    10: "equalizer_current_too_low",
    11: "phase_not_connected",
    25: "limited_by_circuit_fuse",
    26: "limited_by_circuit_max_limit",
    27: "limited_by_circuit_dynamic_limit",
    28: "limited_by_equalizer",
    29: "limited_by_load_balancing",
    30: "limited_by_offline_settings",
    50: "secondary_unit_not_requesting_current",
    51: "max_charger_limit_too_low",
    52: "max_dynamic_charger_limit_too_low",
    53: "charger_disabled",
    54: "waiting_for_schedule_or_authorization",
    55: "pending_authorization",
    56: "charger_in_error_state",
    57: "erratic_ev",
    75: "limited_by_cable_rating",
    76: "limited_by_schedule",
    77: "limited_by_charger_max_limit",
    78: "limited_by_charger_dynamic_limit",
    79: "ev_not_charging",
    80: "limited_by_local_adjustment",
    81: "limited_by_ev",
    100: "undefined",
}

# Codes checked against the app's own wording; the rest are inherited, not verified.
CONFIRMED_REASON_CODES: frozenset[int] = frozenset({25, 53, 55})


def reason_for_no_current(code: int | None) -> str | None:
    """Describe a code, unmapped ones included; display text, so key on the slug."""
    if code is None:
        return None
    return REASON_FOR_NO_CURRENT.get(int(code), f"Unknown ({int(code)})")


def reason_for_no_current_slug(code: int | None) -> str | None:
    """A stable identifier for a reason code; ``"unknown_<code>"`` if unmapped."""
    if code is None:
        return None
    return REASON_FOR_NO_CURRENT_SLUGS.get(int(code), f"unknown_{int(code)}")


from .exceptions import FrameError, IncompleteFrame  # noqa: E402  (re-exported)

# Config fields confirmed by changing the setting and diffing the frame.
CONFIG_FIELDS: dict[int, str] = {
    # Whether the charger is switched on at all - Easee's `isEnabled`.
    1: "isEnabled",
    2: "maxChargerCurrent",
    # Two three-phase current triples: command 50 writes 3/4/5, command 24 writes 8/9/10.
    3: "circuitMaxCurrentP1",
    4: "circuitMaxCurrentP2",
    5: "circuitMaxCurrentP3",
    8: "offlineMaxCircuitCurrentP1",
    9: "offlineMaxCircuitCurrentP2",
    10: "offlineMaxCircuitCurrentP3",
    # 1 = locked 1-phase, 2 = auto, 3 = locked 3-phase; the requested mode, not the one in use.
    7: "phaseMode",
    # The SSID the charger is configured to join - not the site name, and not proof it joined.
    11: "wifiSSID",
    13: "ledStripBrightness",  # 0-100
    # 1 while the cable is locked; absent means unlocked.
    18: "cableLocked",
    # Firmware version, as displayed by the app.
    19: "chargerFirmware",
    # Bluetooth enable mode, written by command 96.
    20: "btEnableMode",
    21: "ipAddress",
    22: "macAddress",
}

# State fields.
STATE_FIELDS: dict[int, str] = {
    # Lifetime energy meter, in kWh.
    3: "lifetimeEnergy",
    # Why the charger is not delivering current; see REASON_FOR_NO_CURRENT.
    5: "reasonForNoCurrent",
    # Total power, in kW (not W). Absent while not charging.
    18: "totalPower",
    # The BLE link's own signal strength, not WiFi or cellular.
    22: "localRSSI",
    # Energy delivered in the current charging session, in kWh.
    19: "sessionEnergy",
    # Dynamic (temporary) charger current, in A.
    11: "dynamicChargerCurrent",
    # Easee's chargerOpMode; see ChargerOpMode.
    12: "chargerOpMode",
    # 25 is the neutral, 26/27/28 the phases. Note this unit charges on L3 when locked to one phase.
    25: "currentN",
    26: "currentL1",
    27: "currentL2",
    28: "currentL3",
    33: "voltageL1N",
    34: "voltageL2N",
    35: "voltageL3N",
    36: "voltageL1L2",
    37: "voltageL2L3",
    38: "voltageL1L3",
    # Cable rating, in A.
    2: "cableRating",
    # The limit an Easee Equalizer imposes on this charger, one field per phase.
    43: "equalizerLimitL1",
    44: "equalizerLimitL2",
    45: "equalizerLimitL3",
}

# Debug fields. Empty on purpose: the temperatures live here but none is confirmed.
DEBUG_FIELDS: dict[int, str] = {}

# Structure fields. A read of this channel is sensitive - it carries key material.
STRUCTURE_FIELDS: dict[int, str] = {
    # The Easee account the charger belongs to; the same value as a read request's `uid`.
    6: "accountId",
}

# Fields whose absence from a frame means zero, not "unknown" (protobuf omits defaults).
ZERO_WHEN_ABSENT: frozenset[str] = frozenset(
    {
        "currentN",
        "currentL1",
        "currentL2",
        "currentL3",
        "totalPower",
        "lifetimeEnergy",
        "reasonForNoCurrent",
        "sessionEnergy",
        "isEnabled",
        "cableLocked",
        # Pausing in the app writes a temporary limit of 0 A, so this field drops out.
        "dynamicChargerCurrent",
        # Listed for the same reason, though a 0 A maximum has never been observed.
        "maxChargerCurrent",
    }
)

# The unit each named field is reported in. Fields with no established unit are absent.
FIELD_UNITS: dict[str, str] = {
    "lifetimeEnergy": "kWh",
    "sessionEnergy": "kWh",
    "totalPower": "kW",
    "currentN": "A",
    "currentL1": "A",
    "currentL2": "A",
    "currentL3": "A",
    "voltageL1N": "V",
    "voltageL2N": "V",
    "voltageL3N": "V",
    "voltageL1L2": "V",
    "voltageL2L3": "V",
    "voltageL1L3": "V",
    "dynamicChargerCurrent": "A",
    "localRSSI": "dBm",
    "maxChargerCurrent": "A",
    "circuitMaxCurrentP1": "A",
    "circuitMaxCurrentP2": "A",
    "circuitMaxCurrentP3": "A",
    "offlineMaxCircuitCurrentP1": "A",
    "offlineMaxCircuitCurrentP2": "A",
    "offlineMaxCircuitCurrentP3": "A",
    "cableRating": "A",
    "equalizerLimitL1": "A",
    "equalizerLimitL2": "A",
    "equalizerLimitL3": "A",
    "ledStripBrightness": "%",
}

# Keyed by `Frame.type`, which is None for a message type we do not recognise.
FIELD_NAMES: dict[MessageType | None, dict[int, str]] = {
    MessageType.CONFIG: CONFIG_FIELDS,
    MessageType.STATE: STATE_FIELDS,
    MessageType.DEBUG: DEBUG_FIELDS,
    MessageType.STRUCTURE: STRUCTURE_FIELDS,
}

# Published names later renamed, old -> current; see "Field name stability" in the README.
FIELD_ALIASES: dict[str, str] = {}


class _AliasedFields(dict[str, Any]):
    """A field mapping that still answers to names this library has renamed."""

    def __init__(self, fields: dict[str, Any]) -> None:
        super().__init__(fields)
        self._renamed = {old: new for old, new in FIELD_ALIASES.items() if new in fields}
        for old, new in self._renamed.items():
            # Never shadow a live name that happens to collide with a retired one.
            if old not in fields:
                super().__setitem__(old, fields[new])

    def _warn(self, key: str) -> None:
        new = self._renamed.get(key)
        if new is not None:
            warnings.warn(
                f"field {key!r} was renamed to {new!r}; it still resolves but "
                f"will be removed in a later release",
                DeprecationWarning,
                stacklevel=3,
            )

    def __getitem__(self, key: str) -> Any:
        self._warn(key)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        self._warn(key)
        return super().get(key, default)


def with_aliases(fields: dict[str, Any]) -> dict[str, Any]:
    """Add any renamed-away names to a field mapping, warning when they are read."""
    return _AliasedFields(fields)


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise FrameError("truncated varint")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _printable(text: str) -> bool:
    return all(c == "\n" or c == "\t" or c.isprintable() for c in text)


def _length_delimited(raw: bytes) -> Any:
    """Interpret a wire-type-2 field without a schema."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and _printable(text):
        return text
    if raw:
        try:
            nested = decode_protobuf(raw)
        except (FrameError, struct.error, IndexError):
            nested = None
        if nested:
            return nested
    return text if text is not None else raw


def decode_protobuf(buf: bytes) -> dict[int, Any]:
    """Decode protobuf into {field number: value} without a schema."""
    out: dict[int, Any] = {}
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
            if value >= 1 << 63:  # two's-complement negative
                value -= 1 << 64
        elif wire == 1:
            value = struct.unpack_from("<d", buf, i)[0]
            i += 8
        elif wire == 2:
            length, i = _varint(buf, i)
            value = _length_delimited(buf[i : i + length])
            i += length
        elif wire == 5:
            value = struct.unpack_from("<f", buf, i)[0]
            i += 4
        else:
            raise FrameError(f"unsupported wire type {wire} for field {number}")
        out[number] = value
    return out


@dataclass(kw_only=True)
class Frame:
    # None when the header carried an unidentified message type; type_id holds the raw byte.
    type: MessageType | None
    raw: bytes
    fields: dict[int, Any] = field(default_factory=dict)
    type_id: int = -1

    def __post_init__(self) -> None:
        if self.type_id < 0 and self.type is not None:
            self.type_id = self.type.value

    @property
    def named(self) -> dict[str, Any]:
        """The fields we have confirmed names for."""
        names = FIELD_NAMES.get(self.type, {})
        out = {names[n]: v for n, v in self.fields.items() if n in names}
        for name in names.values():
            if name not in out and name in ZERO_WHEN_ABSENT:
                out[name] = 0
        return with_aliases(out)

    @property
    def unknown(self) -> dict[int, Any]:
        names = FIELD_NAMES.get(self.type, {})
        return {n: v for n, v in self.fields.items() if n not in names}


def declared_length(data: bytes) -> int | None:
    """The length a frame's header claims, or ``None`` if the header is not all there."""
    if len(data) < HEADER_LEN:
        return None
    return int.from_bytes(data[4:6], "big")


def parse_response(data: bytes) -> Frame:
    """Parse a State or Config response frame; a short one raises IncompleteFrame."""
    if len(data) < HEADER_LEN:
        raise IncompleteFrame(
            f"frame too short to hold a {HEADER_LEN}-byte header: {len(data)} bytes"
        )
    if data[0] != 0x00:
        raise FrameError(f"unexpected first byte {data[0]:#04x}")
    try:
        msg_type: MessageType | None = MessageType(data[1])
    except ValueError:
        # An unknown type is not an error; decode the body and report the raw type.
        msg_type = None
    declared = int.from_bytes(data[4:6], "big")
    if len(data) < declared:
        raise IncompleteFrame(f"frame incomplete: header says {declared} bytes, got {len(data)}")
    if declared != len(data):
        raise FrameError(f"length mismatch: header says {declared}, got {len(data)}")
    return Frame(
        type=msg_type,
        raw=data,
        fields=decode_protobuf(data[HEADER_LEN:]),
        type_id=data[1],
    )


def parse_command_response(data: bytes) -> dict[str, Any]:
    """Parse a Command channel response, which is JSON rather than protobuf."""
    parsed: dict[str, Any] = json.loads(data.decode("utf-8"))
    return parsed


def command_accepted(response: dict[str, Any]) -> bool:
    """Whether the charger accepted a command it replied to."""
    return response.get("code") == 1


def command_payload(response: dict[str, Any]) -> Any | None:
    """Unwrap the JSON some commands hide inside the ``Comment`` string."""
    comment = response.get("Comment")
    if not isinstance(comment, str):
        return None
    try:
        return json.loads(comment)
    except json.JSONDecodeError:
        return None


def command_refusal(response: dict[str, Any]) -> str | None:
    """The charger's stated reason for refusing, if it gave one."""
    if command_accepted(response):
        return None
    comment = response.get("Comment")
    if isinstance(comment, str) and comment:
        return comment
    rest = {k: v for k, v in response.items() if k not in ("id", "code", "Comment")}
    if list(rest.get("res", {})) == ["nws"]:
        del rest["res"]
    if rest:
        return "no reason given; the reply also carried " + ", ".join(
            f"{k}={v!r}" for k, v in sorted(rest.items())
        )
    return "refused without a reason (the reply carried nothing but its outcome)"
