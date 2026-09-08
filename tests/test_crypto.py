"""The record layer, against a real ephemeral session key and nonce."""

from __future__ import annotations

import pytest

from easee_ble import crypto

# Real ephemeral session key; frame re-encrypted from it (see the docstring).
KEY = bytes.fromhex("7809f2dd808b5cdd37be5be226ce9c2b15e5767f4b8192245cb2de216510a446")
FRAME = bytes.fromhex(
    "8b2f999c6cc14ec4eb09fd5536f098a9dbbaa3754f78f1cba93b7179fb977870"
    "d52305b89e608edd5c2e94be5ad23aa75976e1"
)
PLAINTEXT = b'{"uid":1000000,"sn":"EMX00000"}'


def test_decrypt_frame():
    assert crypto.decrypt(KEY, FRAME) == PLAINTEXT


def test_frame_layout_is_nonce_tag_ciphertext():
    # nonce(4) || tag(16) || ciphertext(len(plaintext))
    assert len(FRAME) == crypto.NONCE_LEN + crypto.TAG_LEN + len(PLAINTEXT)


def test_round_trip_with_fixed_nonce():
    frame = crypto.encrypt(KEY, PLAINTEXT, rng=lambda n: b"\x01" * n)
    assert frame[: crypto.NONCE_LEN] == b"\x01" * crypto.NONCE_LEN
    assert crypto.decrypt(KEY, frame) == PLAINTEXT


def test_reencrypting_with_the_same_nonce_reproduces_the_frame():
    nonce = FRAME[: crypto.NONCE_LEN]
    frame = crypto.encrypt(KEY, PLAINTEXT, rng=lambda n: nonce)
    assert frame == FRAME


def test_wrong_key_fails_the_tag():
    with pytest.raises(crypto.RecordError):
        crypto.decrypt(b"\x00" * 32, FRAME)


def test_bad_key_length_rejected():
    with pytest.raises(crypto.RecordError):
        crypto.encrypt(b"short", PLAINTEXT)
