"""Properties the field tables must keep, which a new entry could quietly break."""

from __future__ import annotations

import warnings

import pytest

import easee_ble
from easee_ble import frames
from easee_ble.client import _POLL_REQUESTS, DEFAULT_POLL_CHANNELS
from easee_ble.exceptions import FrameError, IncompleteFrame
from easee_ble.frames import (
    FIELD_ALIASES,
    FIELD_NAMES,
    FIELD_UNITS,
    REASON_FOR_NO_CURRENT,
    REASON_FOR_NO_CURRENT_SLUGS,
    ZERO_WHEN_ABSENT,
    ChargerOpMode,
    Frame,
    MessageType,
    charger_op_mode,
    reason_for_no_current,
    reason_for_no_current_slug,
)


def _all_names() -> dict[str, MessageType]:
    """Every field name the library publishes, and the channel it came from."""
    return {name: mt for mt, table in FIELD_NAMES.items() for name in table.values()}


def test_field_names_are_unique_across_channels():
    """`poll()` merges channels into one flat dict, so a shared name loses one."""
    seen: dict[str, list[str]] = {}
    for message_type, table in FIELD_NAMES.items():
        for name in table.values():
            seen.setdefault(name, []).append(message_type.name)
    clashing = {n: c for n, c in seen.items() if len(c) > 1}
    assert not clashing, f"the same name is published on several channels: {clashing}"


def test_field_numbers_are_unique_within_a_channel():
    for message_type, table in FIELD_NAMES.items():
        names = list(table.values())
        assert len(names) == len(set(names)), f"{message_type.name} names a field twice"


@pytest.mark.parametrize("table_name", ["ZERO_WHEN_ABSENT", "FIELD_UNITS"])
def test_name_keyed_tables_only_mention_real_fields(table_name):
    """Both are keyed by name, so a rename that misses one leaves a dead entry."""
    table = {"ZERO_WHEN_ABSENT": ZERO_WHEN_ABSENT, "FIELD_UNITS": FIELD_UNITS}[table_name]
    dangling = sorted(set(table) - set(_all_names()))
    assert not dangling, f"{table_name} names fields that do not exist: {dangling}"


def test_aliases_point_from_a_retired_name_to_a_live_one():
    names = _all_names()
    for old, new in FIELD_ALIASES.items():
        assert new in names, f"alias {old!r} points at {new!r}, which is not a field"
        assert old not in names, f"{old!r} is both an alias and a live field name"
        assert old not in FIELD_ALIASES.values(), f"{old!r} is on both sides of an alias"


def test_named_answers_to_a_renamed_field_and_says_so(monkeypatch):
    """The rename path itself, exercised with an alias that does not exist yet."""
    monkeypatch.setitem(FIELD_ALIASES, "totalPowerWas", "totalPower")
    named = Frame(type=MessageType.STATE, raw=b"", fields={18: 7.5}).named

    assert named["totalPower"] == 7.5
    with pytest.warns(DeprecationWarning, match="renamed to 'totalPower'"):
        assert named["totalPowerWas"] == 7.5
    with pytest.warns(DeprecationWarning):
        assert named.get("totalPowerWas") == 7.5

    # Iterating is not a deprecated read: a consumer listing the fields it got
    # back should not be warned about a name it never asked for.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert "totalPowerWas" in dict(named)


def test_named_does_not_invent_aliases_for_absent_fields(monkeypatch):
    monkeypatch.setitem(FIELD_ALIASES, "wifiSSIDWas", "wifiSSID")
    named = Frame(type=MessageType.CONFIG, raw=b"", fields={13: 50}).named
    assert "wifiSSIDWas" not in named


def test_reason_slugs_cover_the_same_codes_as_the_descriptions():
    assert set(REASON_FOR_NO_CURRENT_SLUGS) == set(REASON_FOR_NO_CURRENT)
    slugs = list(REASON_FOR_NO_CURRENT_SLUGS.values())
    assert len(slugs) == len(set(slugs)), "two reason codes share a slug"
    assert all(s == s.lower() and s.isidentifier() for s in slugs)


def test_unknown_codes_never_raise():
    """A firmware update must not turn an unseen code into a traceback."""
    assert reason_for_no_current(9999) == "Unknown (9999)"
    assert reason_for_no_current_slug(9999) == "unknown_9999"
    assert charger_op_mode(9999) is None
    assert easee_ble.network_status(9999) is None
    assert reason_for_no_current(None) is None
    assert reason_for_no_current_slug(None) is None
    assert charger_op_mode(None) is None

    assert charger_op_mode(3) is ChargerOpMode.CHARGING
    assert easee_ble.network_status(3) is easee_ble.NetworkStatus.CONNECTED


def test_a_short_frame_is_incomplete_not_malformed():
    """A caller reassembling frames has to tell "read more" from "give up"."""
    whole = bytes([0x00, 0x41, 0x00, 0x00, 0x00, 0x09]) + b"\x90\x01\x01"
    assert frames.parse_response(whole).named["totalPower"] == 1
    assert frames.declared_length(whole) == 9

    with pytest.raises(IncompleteFrame):
        frames.parse_response(whole[:-1])
    with pytest.raises(IncompleteFrame):
        frames.parse_response(whole[:3])
    assert frames.declared_length(whole[:3]) is None

    # Longer than declared is corruption, not a fragment, and stays a FrameError.
    with pytest.raises(FrameError) as caught:
        frames.parse_response(whole + b"\x00")
    assert not isinstance(caught.value, IncompleteFrame)

    # Still catchable the old way.
    with pytest.raises(FrameError):
        frames.parse_response(whole[:-1])


def test_every_default_poll_channel_is_pollable():
    assert set(DEFAULT_POLL_CHANNELS) <= set(_POLL_REQUESTS)


def test_public_api_is_importable():
    """`__all__` is hand-maintained and grouped by role, so it drifts."""
    missing = [name for name in easee_ble.__all__ if not hasattr(easee_ble, name)]
    assert not missing
    assert len(easee_ble.__all__) == len(set(easee_ble.__all__))
