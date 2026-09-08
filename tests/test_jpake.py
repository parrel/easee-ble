"""Check our EC-JPAKE against mbedTLS's own reference handshake."""

from __future__ import annotations

import json
import pathlib

import pytest

from easee_ble.jpake import CLIENT, SERVER, ECJPake, JPakeError

VECTORS = json.loads((pathlib.Path(__file__).parent / "mbedtls_ecjpake_vectors.json").read_text())


def _vec(name: str) -> bytes:
    return bytes.fromhex(VECTORS[name])


def _loaded(role: str, a: str, b: str) -> ECJPake:
    """A context with its two private keys forced to the reference values."""
    ctx = ECJPake(_vec("password"), role=role)
    ctx.xm1 = int.from_bytes(_vec(a), "big")
    ctx.xm2 = int.from_bytes(_vec(b), "big")
    from easee_ble.jpake import _GEN

    ctx.Xm1 = ctx.xm1 * _GEN
    ctx.Xm2 = ctx.xm2 * _GEN
    return ctx


def test_password_is_the_thread_test_vector():
    assert _vec("password") == b"threadjpaketest"


def test_reference_message_sizes():
    # 2 x (point 66 + ZKP 99); this is what makes the shim's 332-byte buffer fit.
    assert len(_vec("cli_one")) == 330
    assert len(_vec("srv_one")) == 330
    # The server alone prefixes ECParameters: named_curve(3) + secp256r1(23).
    assert len(_vec("srv_two")) == 168
    assert _vec("srv_two")[:3] == bytes([3, 0, 23])
    assert len(_vec("cli_two")) == 165


def test_client_derives_mbedtls_session_key():
    cli = _loaded(CLIENT, "x1", "x2")
    cli.read_round_one(_vec("srv_one"))
    cli.read_round_two(_vec("srv_two"))
    assert cli.derive_secret() == _vec("pms")


def test_server_derives_mbedtls_session_key():
    srv = _loaded(SERVER, "x3", "x4")
    srv.read_round_one(_vec("cli_one"))
    srv.read_round_two(_vec("cli_two"))
    assert srv.derive_secret() == _vec("pms")


def test_round_trip_between_our_own_endpoints():
    """Covers the write path, which the reference vectors cannot."""
    secret = b"1234"
    cli, srv = ECJPake(secret, CLIENT), ECJPake(secret, SERVER)
    c1, s1 = cli.write_round_one(), srv.write_round_one()
    # Two proofs per round-one message, so up to two bytes short of 330.
    assert 328 <= len(c1) <= 330 and 328 <= len(s1) <= 330
    cli.read_round_one(s1)
    srv.read_round_one(c1)
    c2, s2 = cli.write_round_two(), srv.write_round_two()
    # One proof each; the server's is 3 bytes longer for its ECParameters.
    assert 164 <= len(c2) <= 165 and 167 <= len(s2) <= 168
    cli.read_round_two(s2)
    srv.read_round_two(c2)
    key = cli.derive_secret()
    assert key == srv.derive_secret()
    assert len(key) == 32


def test_mismatched_password_fails_to_verify():
    cli, srv = ECJPake(b"1234", CLIENT), ECJPake(b"9999", SERVER)
    cli.read_round_one(srv.write_round_one())
    srv.read_round_one(cli.write_round_one())
    cli.read_round_two(srv.write_round_two())
    srv.read_round_two(cli.write_round_two())
    # A wrong password still completes the exchange, it just yields a different key.
    assert cli.derive_secret() != srv.derive_secret()


def test_tampered_proof_is_rejected():
    cli, srv = ECJPake(b"1234", CLIENT), ECJPake(b"1234", SERVER)
    msg = bytearray(srv.write_round_one())
    msg[-1] ^= 0x01
    with pytest.raises(JPakeError):
        cli.read_round_one(bytes(msg))


def test_round_two_rejects_a_different_curve():
    cli, srv = ECJPake(b"1234", CLIENT), ECJPake(b"1234", SERVER)
    cli.read_round_one(srv.write_round_one())
    srv.read_round_one(cli.write_round_one())
    msg = bytearray(srv.write_round_two())
    msg[2] = 24  # secp384r1
    with pytest.raises(JPakeError, match="secp256r1"):
        cli.read_round_two(bytes(msg))
