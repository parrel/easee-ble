"""Request builders, checked against the exact bytes the app sends."""

from __future__ import annotations

import inspect
import json

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


def test_charger_enabled_is_command_29():
    """Command 29 is the app's SetEnabled, not an access-control setting."""
    assert commands.set_charger_enabled(True) == b'{"Id":29,"Arguments":[{"Id":1,"Value":"true"}]}'


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


def test_rfid_requests_have_no_arguments_array():
    """The token commands put parameters at the top level, not in Arguments."""
    assert commands.list_local_rfids() == b'{"Id":102}'
    assert commands.get_local_rfid("Alice") == b'{"Id":101,"us":"1","un":"Alice"}'
    assert commands.add_local_rfid("Alice", "userid_42") == (
        b'{"Id":98,"us":"1","un":"Alice","ut":"userid_42"}'
    )


def test_play_lights_matches_the_app():
    assert commands.play_lights() == (
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


def test_command_ids_match_the_apps_dispatch_table():
    """Spot-checks of the table recovered from the app; see research/docs/APP-MODEL.md."""
    assert commands.Command.REBOOT == 1
    assert commands.Command.SCAN_WIFI == 7
    assert commands.Command.SET_ENABLED == 29
    assert commands.Command.SET_RFID_PAIRING_MODE == 69
    assert commands.Command.CLEAR_LOCAL_RFIDS == 100
    assert commands.Command.REMOVE_LOCAL_RFID == 99


def test_pause_and_resume_are_a_dynamic_current_write():
    """The app implements both as command 48."""
    assert commands.pause_charging() == commands.set_dynamic_charger_current(0)
    assert commands.resume_charging(10) == commands.set_dynamic_charger_current(10)
    with pytest.raises(ValueError):
        commands.resume_charging(0)


def test_key_requests_carry_no_arguments_array():
    assert commands.clear_local_rfids() == b'{"Id":100}'
    assert commands.remove_local_rfid("12345678") == b'{"Id":99,"ut":"12345678"}'


def test_every_command_id_has_a_builder():
    """The public surface is the documentation: one function per id, nothing id-only."""
    built = set()
    for name, fn in vars(commands).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        if name in {"read_request", "write_request", "network_status"}:
            continue
        args = [
            {"int": 1, "str": "x", "bool": True}.get(p.annotation, 1)
            for p in inspect.signature(fn).parameters.values()
            if p.default is inspect.Parameter.empty
        ]
        built.add(json.loads(fn(*args))["Id"])
    assert built == {int(c) for c in commands.Command}


def test_argument_less_commands_are_sent_bare():
    assert commands.reboot() == b'{"Id":1}'
    assert commands.run_self_test() == b'{"Id":16}'
    assert commands.factory_reset() == b'{"Id":97}'
    assert commands.get_mid_public_key() == b'{"Id":110}'


def test_shapes_confirmed_on_a_charger():
    assert commands.set_rfid_pairing_mode(60) == b'{"Id":69,"Arguments":[{"Id":1,"Value":"60"}]}'
    assert commands.display_mid_public_key() == (
        b'{"Id":115,"Arguments":[{"Id":1,"Value":"true"}]}'
    )
    assert commands.set_fallback_circuit_current(12, 12, 12) == (
        b'{"Id":21,"Arguments":[{"Id":1,"Value":"12"},{"Id":2,"Value":"12"},{"Id":3,"Value":"12"}]}'
    )
    assert commands.set_wifi("", "") == (
        b'{"Id":53,"Arguments":[{"Id":1,"Value":""},{"Id":2,"Value":""}]}'
    )


def test_authorisation_shapes_read_off_the_app():
    assert commands.authorize_charging("userid_7") == (
        b'{"Id":25,"Arguments":[{"Id":1,"Value":"userid_7"},{"Id":2,"Value":"42"}]}'
    )
    assert commands.deauthorize_charging("userid_7") == (
        b'{"Id":26,"Arguments":[{"Id":1,"Value":"userid_7"},{"Id":2,"Value":"0"}]}'
    )


def test_fuse_is_command_50():
    assert commands.set_circuit_rated_current(16) == (
        b'{"Id":50,"Arguments":[{"Id":1,"Value":"16"}]}'
    )
