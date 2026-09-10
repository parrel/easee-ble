"""Local BLE control for Easee EV chargers."""

from __future__ import annotations

from . import commands, crypto, frames, protocol
from .client import DEFAULT_CHANNELS, DEFAULT_POLL_CHANNELS, EaseeCharger
from .commands import (
    BtEnableMode,
    Command,
    NetworkStatus,
    PhaseMode,
    network_status,
)
from .const import HANDSHAKE_MTU
from .crypto import decrypt, encrypt
from .exceptions import (
    EaseeCommandRefused,
    EaseeConnectionError,
    EaseeError,
    FrameError,
    IncompleteFrame,
    JPakeError,
    ProtocolError,
    RecordError,
    SessionError,
)
from .frames import (
    FIELD_ALIASES,
    FIELD_UNITS,
    REASON_FOR_NO_CURRENT,
    REASON_FOR_NO_CURRENT_SLUGS,
    ZERO_WHEN_ABSENT,
    ChargerOpMode,
    Frame,
    LedMode,
    MessageType,
    charger_op_mode,
    command_accepted,
    command_payload,
    command_refusal,
    declared_length,
    led_mode,
    parse_command_response,
    parse_response,
    reason_for_no_current,
    reason_for_no_current_slug,
    wifi_networks,
    with_aliases,
)
from .jpake import ECJPake
from .protocol import CHANNEL_BY_UUID, MANUFACTURER_ID, SERVICE_UUID, Channel
from .session import Request, Session

__all__ = [
    # client
    "EaseeCharger",
    # sans-io core
    "Session",
    "Request",
    "ECJPake",
    "encrypt",
    "decrypt",
    # commands and their value types
    "Command",
    "PhaseMode",
    "BtEnableMode",
    "NetworkStatus",
    "network_status",
    # responses
    "Frame",
    "MessageType",
    "ChargerOpMode",
    "charger_op_mode",
    "LedMode",
    "led_mode",
    "REASON_FOR_NO_CURRENT",
    "REASON_FOR_NO_CURRENT_SLUGS",
    "FIELD_UNITS",
    "reason_for_no_current",
    "reason_for_no_current_slug",
    "ZERO_WHEN_ABSENT",
    # renaming a shipped field name; see "Field name stability" in the README
    "FIELD_ALIASES",
    "with_aliases",
    "declared_length",
    "parse_response",
    "parse_command_response",
    "command_accepted",
    "command_payload",
    "wifi_networks",
    "command_refusal",
    # transport constants worth knowing about
    "SERVICE_UUID",
    "MANUFACTURER_ID",
    "CHANNEL_BY_UUID",
    "Channel",
    "HANDSHAKE_MTU",
    "DEFAULT_CHANNELS",
    "DEFAULT_POLL_CHANNELS",
    # exceptions
    "EaseeError",
    "EaseeConnectionError",
    "EaseeCommandRefused",
    "ProtocolError",
    "FrameError",
    "IncompleteFrame",
    "RecordError",
    "SessionError",
    "JPakeError",
    # submodules
    "commands",
    "crypto",
    "frames",
    "protocol",
]

__version__ = "0.1.1"
