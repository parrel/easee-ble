"""Request builders, checked against the exact bytes the app sends."""

from __future__ import annotations

import pytest

from easee_ble import commands
from easee_ble.commands import Command, PhaseMode


def test_set_led_brightness_matches_the_app():
    # Decrypted verbatim from a real "set LED to 75" command.
    assert commands.set_led_brightness(75) == b'{"Id":40,"Arguments":[{"Id":1,"Value":"75"}]}'


def test_confirmed_commands_match_decrypted_vectors():
    # Each of these was decrypted from a real command issued from the app.
    assert commands.set_phase_mode(PhaseMode.AUTO) == (
        b'{"Id":38,"Arguments":[{"Id":1,"Value":"2"}]}'
    )
    assert commands.set_max_charger_current(11) == b'{"Id":47,"Arguments":[{"Id":1,"Value":"11"}]}'
    assert commands.set_dynamic_charger_current(15) == (
        b'{"Id":48,"Arguments":[{"Id":1,"Value":"15"}]}'
    )
    assert commands.set_charger_enabled(False) == (
        b'{"Id":29,"Arguments":[{"Id":1,"Value":"false"}]}'
    )


def test_booleans_are_lowercase():
    assert commands.set_charger_enabled(True) == b'{"Id":29,"Arguments":[{"Id":1,"Value":"true"}]}'


def test_the_old_access_control_name_still_works():
    """It was a misnomer - command 29 is the on/off switch - but callers exist."""
    assert commands.set_access_control(True) == commands.set_charger_enabled(True)
    assert commands.Command.SET_ACCESS_CONTROL is commands.Command.SET_CHARGER_ENABLED


def test_set_led_brightness_range():
    for bad in (-1, 101):
        with pytest.raises(ValueError):
            commands.set_led_brightness(bad)


def test_read_request_shape():
    # Decrypted verbatim from a real State/Config poll.
    assert commands.read_request(1000000, "EMX00000") == b'{"uid":1000000,"sn":"EMX00000"}'


def test_write_request_coerces_values_to_strings():
    assert commands.write_request(Command.SET_LED_BRIGHTNESS, [(1, 30)]) == (
        b'{"Id":40,"Arguments":[{"Id":1,"Value":"30"}]}'
    )


def test_user_token_requests_have_no_arguments_array():
    """The token commands put parameters at the top level, not in Arguments."""
    assert commands.list_user_tokens() == b'{"Id":102}'
    assert commands.get_user_token(1, "Alice") == b'{"Id":101,"us":"1","un":"Alice"}'
    assert commands.set_user_token(1, "Alice", "userid_42") == (
        b'{"Id":98,"us":"1","un":"Alice","ut":"userid_42"}'
    )


def test_open_session_matches_the_app():
    assert commands.open_session() == (
        b'{"Id":32,"Arguments":[{"Id":1,"Value":"46"},{"Id":2,"Value":"2"}]}'
    )


def test_command_payload_unwraps_nested_json():
    from easee_ble.frames import command_payload, parse_command_response

    reply = parse_command_response(
        b'{"id":102,"code":1,"resultcode":0,"Comment":"{\\"utns\\":[\\"Alice\\"]}","res":{"nws":3}}'
    )
    assert command_payload(reply) == {"utns": ["Alice"]}


def test_command_payload_ignores_prose_comments():
    from easee_ble.frames import command_payload, parse_command_response

    reply = parse_command_response(b'{"id":41,"code":0,"Comment":"command 41 not supported"}')
    assert command_payload(reply) is None
