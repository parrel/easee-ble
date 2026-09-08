"""The Easee BLE record layer: AES-256-GCM keyed on the EC-JPAKE session key."""

from __future__ import annotations

import os
from collections.abc import Callable

from Crypto.Cipher import AES

# Bytes of random nonce prepended to every request frame.
NONCE_LEN = 4
# GCM authentication tag length.
TAG_LEN = 16
# The session key is a 32-byte (AES-256) key.
KEY_LEN = 32

_OVERHEAD = NONCE_LEN + TAG_LEN


from .exceptions import RecordError  # noqa: E402  (re-exported for callers)


def encrypt(key: bytes, plaintext: bytes, *, rng: Callable[[int], bytes] = os.urandom) -> bytes:
    """Encrypt a request into a ``nonce || tag || ciphertext`` frame."""
    if len(key) != KEY_LEN:
        raise RecordError(f"key must be {KEY_LEN} bytes, got {len(key)}")
    nonce = rng(NONCE_LEN)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_LEN)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return nonce + tag + ciphertext


def decrypt(key: bytes, frame: bytes) -> bytes:
    """Reverse :func:`encrypt`, verifying the tag."""
    if len(key) != KEY_LEN:
        raise RecordError(f"key must be {KEY_LEN} bytes, got {len(key)}")
    if len(frame) < _OVERHEAD:
        raise RecordError(f"frame too short: {len(frame)} bytes")
    nonce = frame[:NONCE_LEN]
    tag = frame[NONCE_LEN:_OVERHEAD]
    ciphertext = frame[_OVERHEAD:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_LEN)
    try:
        return cipher.decrypt_and_verify(ciphertext, tag)
    except ValueError as exc:
        raise RecordError("tag did not verify (wrong session key?)") from exc
