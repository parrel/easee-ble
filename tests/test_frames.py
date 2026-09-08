"""Frame decoding, checked against frames captured from a real charger."""

from __future__ import annotations

import pytest

from easee_ble import frames
from easee_ble.frames import (
    ChargerOpMode,
    Frame,
    FrameError,
    MessageType,
    command_accepted,
    command_refusal,
    parse_command_response,
    parse_response,
)

# A real Config frame, with the site name, IP and MAC replaced by same-length dummies.
CONFIG_BEFORE = bytes.fromhex(
    "00400101005b08011500000042181020102810300138024028482850285a0a45"
    "78616d706c65536974684b78019001019801d702a00101aa010d3139382e3531"
    "2e3130302e3432b2011130303a30303a35653a30303a35333a6166"
)

# A real State frame from the same session, before the current was changed.
STATE_SAMPLE = bytes.fromhex(
    "00410101009c101419713d0ad7a3b03440202d2837300358206007b001b7ffff"
    "ffffffffffff01b801cfffffffffffffffff01c00101cd016f12833ad501f4fd"
    "543ddd0160e5503de501cdcc4c3ded013333f33e8d02b23d6c439502d5586c43"
    "9d02acfc6b43a5025c8fcc43ad026d67cc43b5024e62cc43bd028716593ec502"
    "b6f37d3ddd0200009841e5020000b041ed020000b841f00219f80201"
)
# A real State frame captured while the charger was actively charging.
STATE_CHARGING = bytes.fromhex(
    "0041010100b6101419295c8fc2f54841402038281c3003580f6003681e8d0100"
    "00c040950158392c409d017b145e41a501cdccac3fb001b0ffffffffffffffff"
    "01b801cfffffffffffffffff01c00101cd0114ae9f40d501d9ce373fdd015839"
    "bc40e501b4c8b640ed01e17a943f8d029eef6d439502e77b6c439d02982e6a43"
    "a5021deacc43ad0208eccb43b5029aa9cb43bd02c9761e3fc50279696c43dd02"
    "0000c040e5020000c040ed020000c040f00218f80201"
)

COMMAND_OK = b'{"id":40,"code":1,"res":{"nws":3}}'


def _config(phase_mode: int, led: int) -> bytes:
    """Rebuild a minimal Config frame with the two confirmed fields."""
    body = bytes([0x38, phase_mode]) + bytes([0x68, led])
    return bytes([0x00, 0x40, 0x01, 0x01]) + (len(body) + 6).to_bytes(2, "big") + body


def test_header_type_and_length():
    frame = parse_response(_config(2, 75))
    assert frame.type is MessageType.CONFIG
    assert frame.raw[:2] == b"\x00\x40"


def test_confirmed_config_fields():
    before = parse_response(_config(2, 75))
    after = parse_response(_config(1, 100))
    assert before.named["phaseMode"] == 2  # auto
    assert after.named["phaseMode"] == 1  # locked to single phase
    assert before.named["ledStripBrightness"] == 75
    assert after.named["ledStripBrightness"] == 100


def test_state_dynamic_current():
    body = bytes([0x58, 32])
    frame = parse_response(bytes([0, 0x41, 1, 1]) + (len(body) + 6).to_bytes(2, "big") + body)
    assert frame.type is MessageType.STATE
    assert frame.named["dynamicChargerCurrent"] == 32


def test_unknown_fields_are_kept_by_number():
    body = bytes([0xF8, 0x3F, 0x07])  # field 1023, varint 7
    frame = parse_response(bytes([0, 0x41, 1, 1]) + (len(body) + 6).to_bytes(2, "big") + body)
    assert frame.unknown[1023] == 7
    assert "1023" not in frame.named


def test_length_mismatch_is_rejected():
    body = bytes([0x58, 32])
    # Longer than declared is corruption; short of it is IncompleteFrame instead.
    bad = bytes([0, 0x41, 1, 1]) + (7).to_bytes(2, "big") + body
    with pytest.raises(FrameError, match="length mismatch"):
        parse_response(bad)


def test_unknown_message_type_is_kept_raw():
    """An unrecognised message type is kept as a raw int, not rejected."""
    frame = parse_response(bytes([0, 0x99, 1, 1, 0, 6]))
    assert frame.type is None
    assert frame.type_id == 0x99
    assert frame.fields == {}
    assert frame.named == {}


def test_real_config_frame_from_capture():
    frame = parse_response(CONFIG_BEFORE)
    assert frame.type is MessageType.CONFIG
    assert frame.named["phaseMode"] == 2
    assert frame.named["ledStripBrightness"] == 75
    assert frame.named["maxChargerCurrent"] == pytest.approx(32.0)


def test_real_state_frame_from_capture():
    frame = parse_response(STATE_SAMPLE)
    assert frame.type is MessageType.STATE
    assert frame.named["dynamicChargerCurrent"] == 32
    # Three mains voltages, all plausibly around 230 V.
    for phase in ("voltageL1N", "voltageL2N", "voltageL3N"):
        assert 200 < frame.named[phase] < 260
    # Line-to-line voltages are sqrt(3) higher.
    assert frame.named["voltageL1L2"] == pytest.approx(frame.named["voltageL1N"] * 3**0.5, rel=0.02)


def test_charger_op_mode_from_capture():
    frame = parse_response(STATE_CHARGING)
    assert frame.type is MessageType.STATE
    assert frame.named["chargerOpMode"] == ChargerOpMode.CHARGING
    assert frame.named["dynamicChargerCurrent"] == 15


def test_negative_varints_are_signed():
    """The charger reports small negatives as two's complement 64-bit varints."""
    frame = parse_response(STATE_SAMPLE)
    # Field 22 is the BLE RSSI, so a negative value is the check that it decodes as signed.
    assert frame.named["localRSSI"] == -73
    assert frame.unknown[23] == -49


def test_command_response_is_json():
    parsed = parse_command_response(COMMAND_OK)
    assert parsed == {"id": 40, "code": 1, "res": {"nws": 3}}


# A real Structure frame (message type 0x42), the first this project has seen.
STRUCTURE_SAMPLE = bytes.fromhex(
    "0042010100ad0a0e3030303030303030303030303030120e3030303030303030"
    "30303030303018b0e0c08183868c98302a184141414141414141414141414141"
    "41414141414141413d3d320630303030303048195001581062510a3141414141"
    "4141414141414141414141414141414141413d3d3b4242424242424242424242"
    "42424242424242424242423d3d1a0676312e312d622210323032362d30392d30"
    "335431303a30312a024e4c6803"
)

# A real Debug frame (message type 0x43), ICCID redacted.
DEBUG_SAMPLE = bytes.fromhex(
    "0043010100980811100135f4fd543d3df4fd543d45f4fd543d480150015a0142"
    "6816721438393030303030303030303030303030303030307a1c424739354d33"
    "4c415230324130335f41302e3330312e41302e3330318001038801089001069d"
    "01e17a0c41a5013bdf0b41a80112b00104c00101c80117d00116d80116e00116"
    "e80116f00117f80116800216880216900216980203a00234"
)

# The Charge Max refusing a command id it does not implement.
COMMAND_UNSUPPORTED = (
    b'{"id":41,"code":0,"resultcode":0,"Comment":"command 41 not supported","res":{"nws":3}}'
)


def test_structure_frame_type():
    frame = parse_response(STRUCTURE_SAMPLE)
    assert frame.type is MessageType.STRUCTURE
    assert frame.type_id == 0x42


def test_structure_nested_message_is_decoded():
    """Field 12 is a nested message, not a string - the decoder must recurse."""
    nested = parse_response(STRUCTURE_SAMPLE).fields[12]
    assert isinstance(nested, dict)
    assert nested[3] == "v1.1-b"
    assert nested[5] == "NL"


def test_debug_frame_is_decoded_but_not_named():
    """The Debug frame decodes; nothing in it has earned a name."""
    frame = parse_response(DEBUG_SAMPLE)
    assert frame.type is MessageType.DEBUG
    assert frame.named == {}
    assert frame.fields[14].startswith("89")
    assert len(frame.fields[14]) == 20
    assert frame.fields[15].startswith("BG95")


# A State frame captured while the charger's own meter read ~43 kWh.
STATE_WITH_METER = bytes.fromhex(
    "0041010100911014190ad7a3703d2a45402044283430036007b001b7ffffffff"
    "ffffffff01b801cfffffffffffffffff01c00101d501f4fd543ddd0160e5503d"
    "e50160e5503ded018716193f8d02084c6d439502cb616e439d0210186d43a502"
    "a430ce43ad02aaa1cd43b5024686cd43bd020681153ec50283c0ca3ddd020000"
    "b841e5020000b841ed020000b041f80201"
)


def test_lifetime_energy_matches_the_meter():
    """State field 3 read 42.33 with the charger's own meter showing ~43 kWh."""
    frame = parse_response(STATE_WITH_METER)
    assert frame.named["lifetimeEnergy"] == pytest.approx(42.33)


def test_command_refusal_is_not_success():
    response = parse_command_response(COMMAND_UNSUPPORTED)
    assert not command_accepted(response)
    assert command_refusal(response) == "command 41 not supported"


def test_refusal_without_a_comment_shows_what_the_reply_did_carry():
    """A refused value comes back bare - so report the undecoded fields."""
    reason = command_refusal({"id": 47, "code": 0, "resultcode": 7})
    assert reason is not None
    assert "resultcode=7" in reason


def test_a_refusal_carrying_only_the_network_status_is_a_bare_refusal():
    """`res.nws` rides on every ack, so it explains nothing about a refusal."""
    assert command_refusal({"id": 47, "code": 0, "res": {"nws": 3}}) == (
        "refused without a reason (the reply carried nothing but its outcome)"
    )


def test_refusal_with_nothing_but_an_outcome_says_so():
    assert command_refusal({"id": 47, "code": 0}) == (
        "refused without a reason (the reply carried nothing but its outcome)"
    )


def test_command_success_has_no_refusal():
    response = parse_command_response(COMMAND_OK)
    assert command_accepted(response)
    assert command_refusal(response) is None


# Both taken from one capture, thirty seconds apart, either side of an app write.
CONFIG_CHARGER_ON = bytes.fromhex(
    "00400101005b08011500003041181020102810300138024028482850285a0a45"
    "78616d706c65536974686478019001019801d702a00103aa010d3139382e3531"
    "2e3130302e3432b2011130303a30303a35653a30303a35333a6166"
)
CONFIG_CHARGER_OFF = bytes.fromhex(
    "0040010100591500003041181020102810300138024028482850285a0a457861"
    "6d706c65536974686478019001019801d702a00103aa010d3139382e35312e31"
    "30302e3432b2011130303a30303a35653a30303a35333a6166"
)
# The State frame from the middle of that charging session.
STATE_CHARGING = bytes.fromhex(
    "0041010100b610141985eb51b81e4541402038281c3003580f6003681e8d0100"
    "00c040950158392c409d019a995d41a501cdccac3fb001aeffffffffffffffff"
    "01b801cfffffffffffffffff01c00101cd016f129f40d5016210383fdd01d7a3"
    "bc40e5015a64b740ed016f12c33f8d0239f46e439502148e6b439d027f4a6943"
    "a502d9cecc43ad0281d5cb43b50221e0ca43bd02d578e93ec50214ee6c43dd02"
    "0000c040e5020000c040ed020000c040f00218f80201"
)


def test_is_enabled_is_true_while_the_charger_is_on():
    assert parse_response(CONFIG_CHARGER_ON).named["isEnabled"] == 1


def test_is_enabled_reads_false_when_the_field_is_absent():
    # Protobuf omits a field at its default, so "off" arrives as no field at all.
    frame = parse_response(CONFIG_CHARGER_OFF)
    assert 1 not in frame.fields
    assert frame.named["isEnabled"] == 0


def test_absent_zero_current_reads_as_zero_not_missing():
    # currentN drops out of roughly half of all State frames, rounding to 0.000.
    frame = parse_response(STATE_CHARGING)
    assert frame.named["currentN"] > 0
    off = parse_response(STATE_SAMPLE)
    assert "currentN" in off.named


def test_paused_temporary_limit_reads_as_zero_not_missing():
    # Pausing in the Easee app sets the temporary limit to 0 A, so field 11 vanishes.
    charging = parse_response(STATE_CHARGING)
    assert charging.named["dynamicChargerCurrent"] == 15
    paused = Frame(type=MessageType.STATE, raw=b"", fields={12: 2})
    assert 11 not in paused.fields
    assert paused.named["dynamicChargerCurrent"] == 0


def test_zero_max_charger_current_reads_as_zero_not_missing():
    # A maximum of 0 A is writable, so its absence must read as 0 rather than unknown.
    configured = parse_response(CONFIG_BEFORE)
    assert configured.named["maxChargerCurrent"] == 32
    zeroed = Frame(type=MessageType.CONFIG, raw=b"", fields={1: 1})
    assert 2 not in zeroed.fields
    assert zeroed.named["maxChargerCurrent"] == 0


def test_fields_not_in_the_zero_list_are_not_invented():
    # A missing voltage is ignorance, not 0 V, so it must not be filled in.
    from easee_ble.frames import ZERO_WHEN_ABSENT

    assert "voltageL1N" not in ZERO_WHEN_ABSENT
    stripped = Frame(type=MessageType.STATE, raw=b"", fields={12: 2})
    assert "voltageL1N" not in stripped.named
    assert stripped.named["currentN"] == 0  # this one is on the list


def test_total_power_matches_the_measured_phases():
    """The arithmetic this field's name rests on."""
    f = parse_response(STATE_CHARGING).named
    measured = (f["currentL2"] * f["voltageL2N"] + f["currentL3"] * f["voltageL3N"]) / 1000
    assert abs(f["totalPower"] - measured) / measured < 0.02


def test_reason_for_no_current_names_the_disabled_charger():
    from easee_ble.frames import reason_for_no_current

    assert reason_for_no_current(53) == "Charger disabled"
    assert reason_for_no_current(None) is None
    # An unmapped code must still be reportable rather than swallowed.
    assert "9999" in reason_for_no_current(9999)


def test_field_units_only_names_fields_that_exist():
    """A unit for a field name nothing produces is a unit nobody will ever see."""
    known = set(frames.CONFIG_FIELDS.values()) | set(frames.STATE_FIELDS.values())
    assert set(frames.FIELD_UNITS) <= known


def test_power_and_energy_units_are_kilo():
    """Guarded because getting these wrong scales a reading by 1000 silently."""
    assert frames.FIELD_UNITS["totalPower"] == "kW"
    assert frames.FIELD_UNITS["lifetimeEnergy"] == "kWh"
    assert frames.FIELD_UNITS["sessionEnergy"] == "kWh"
