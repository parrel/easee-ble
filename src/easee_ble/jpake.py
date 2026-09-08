"""EC-JPAKE, wire-compatible with the mbedTLS implementation Easee's app uses."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable

from ecdsa import NIST256p
from ecdsa.ellipticcurve import AbstractPoint, Point

_CURVE = NIST256p
_GEN = _CURVE.generator
_N = _CURVE.order
_FP = _CURVE.curve
_COORD_LEN = 32

# TLS named-curve id for secp256r1, as written in the round-two ECParameters.
_TLS_ID_SECP256R1 = 23
_TLS_NAMED_CURVE = 3

CLIENT = "client"
SERVER = "server"


from .exceptions import JPakeError  # noqa: E402  (re-exported for callers)

# --- encoding helpers --------------------------------------------------------


def _affine(P: AbstractPoint) -> Point:
    """Normalise a possibly-Jacobian point so coordinates can be read."""
    return P.to_affine() if hasattr(P, "to_affine") else P


def _point_to_bytes(P: AbstractPoint) -> bytes:
    P = _affine(P)
    x: int = P.x()
    y: int = P.y()
    return b"\x04" + x.to_bytes(_COORD_LEN, "big") + y.to_bytes(_COORD_LEN, "big")


def _point_from_bytes(raw: bytes) -> Point:
    if len(raw) != 1 + 2 * _COORD_LEN or raw[0] != 0x04:
        raise JPakeError("expected a 65-byte uncompressed point")
    x = int.from_bytes(raw[1 : 1 + _COORD_LEN], "big")
    y = int.from_bytes(raw[1 + _COORD_LEN :], "big")
    if not _FP.contains_point(x, y):
        raise JPakeError("point is not on the curve")
    return Point(_FP, x, y)


def _write_tls_point(P: AbstractPoint) -> bytes:
    """TLS ECPoint: one length byte, then the point."""
    body = _point_to_bytes(P)
    return bytes([len(body)]) + body


def _read_tls_point(buf: bytes, off: int) -> tuple[Point, int]:
    if off >= len(buf):
        raise JPakeError("truncated point")
    n = buf[off]
    off += 1
    if off + n > len(buf):
        raise JPakeError("truncated point")
    return _point_from_bytes(buf[off : off + n]), off + n


def _hash_point(P: AbstractPoint) -> bytes:
    """Point as fed to the ZKP hash: four-byte big-endian length, then the point."""
    body = _point_to_bytes(P)
    return len(body).to_bytes(4, "big") + body


def _zkp_hash(G: AbstractPoint, V: AbstractPoint, X: AbstractPoint, ident: str) -> int:
    ident_bytes = ident.encode()
    buf = (
        _hash_point(G)
        + _hash_point(V)
        + _hash_point(X)
        + len(ident_bytes).to_bytes(4, "big")
        + ident_bytes
    )
    digest = int.from_bytes(hashlib.sha256(buf).digest(), "big")
    return digest % int(_N)


def _int_to_minimal(value: int) -> bytes:
    """Big-endian with no leading zero bytes, as mbedtls_mpi_write_binary does."""
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


# --- the protocol ------------------------------------------------------------


class ECJPake:
    """One side of an EC-JPAKE exchange."""

    def __init__(
        self,
        secret: bytes,
        role: str = CLIENT,
        *,
        rng: Callable[[int], bytes] = os.urandom,
    ) -> None:
        if role not in (CLIENT, SERVER):
            raise ValueError(f"role must be {CLIENT!r} or {SERVER!r}")
        self.role = role
        self.peer_role = SERVER if role == CLIENT else CLIENT
        self._rng = rng
        # mbedTLS reads the shared secret as a plain big-endian integer.
        self.s = int.from_bytes(secret, "big")

        self.xm1 = self.xm2 = 0
        self.Xm1 = self.Xm2 = None
        self.Xp1 = self.Xp2 = self.Xp = None

    # -- key generation --

    def _scalar(self) -> int:
        while True:
            k = int.from_bytes(self._rng(_COORD_LEN), "big")
            if 1 <= k < _N:
                return k

    def _keypair(self, G: AbstractPoint) -> tuple[int, AbstractPoint]:
        x = self._scalar()
        return x, x * G

    # -- zero-knowledge proofs --

    def _zkp_write(self, G: AbstractPoint, x: int, X: AbstractPoint, ident: str) -> bytes:
        v, V = self._keypair(G)
        h = _zkp_hash(G, V, X, ident)
        r = (v - x * h) % _N
        r_bytes = _int_to_minimal(r)
        return _write_tls_point(V) + bytes([len(r_bytes)]) + r_bytes

    def _zkp_read(
        self, G: AbstractPoint, X: AbstractPoint, ident: str, buf: bytes, off: int
    ) -> int:
        V, off = _read_tls_point(buf, off)
        if off >= len(buf):
            raise JPakeError("truncated proof")
        r_len = buf[off]
        off += 1
        if r_len == 0 or off + r_len > len(buf):
            raise JPakeError("truncated proof")
        r = int.from_bytes(buf[off : off + r_len], "big")
        off += r_len
        h = _zkp_hash(G, V, X, ident)
        if _affine(h * X + r * G) != _affine(V):
            raise JPakeError("Schnorr proof did not verify (wrong PIN?)")
        return off

    def _kkp_write(self, G: AbstractPoint, ident: str) -> tuple[int, AbstractPoint, bytes]:
        x, X = self._keypair(G)
        return x, X, _write_tls_point(X) + self._zkp_write(G, x, X, ident)

    def _kkp_read(
        self, G: AbstractPoint, ident: str, buf: bytes, off: int
    ) -> tuple[AbstractPoint, int]:
        X, off = _read_tls_point(buf, off)
        off = self._zkp_read(G, X, ident, buf, off)
        return X, off

    # -- round one --

    def write_round_one(self) -> bytes:
        self.xm1, self.Xm1, first = self._kkp_write(_GEN, self.role)
        self.xm2, self.Xm2, second = self._kkp_write(_GEN, self.role)
        return first + second

    def read_round_one(self, data: bytes) -> None:
        self.Xp1, off = self._kkp_read(_GEN, self.peer_role, data, 0)
        self.Xp2, off = self._kkp_read(_GEN, self.peer_role, data, off)
        if off != len(data):
            raise JPakeError("trailing bytes after round one")

    # -- round two --

    def write_round_two(self) -> bytes:
        if self.Xp1 is None or self.Xp2 is None:
            raise JPakeError("read_round_one must happen first")
        G = self.Xp1 + self.Xp2 + self.Xm1
        xm = (self.xm2 * self.s) % _N
        Xm = xm * G
        out = b""
        if self.role == SERVER:
            # Only the server prefixes its message with ECParameters.
            out += bytes([_TLS_NAMED_CURVE]) + _TLS_ID_SECP256R1.to_bytes(2, "big")
        return out + _write_tls_point(Xm) + self._zkp_write(G, xm, Xm, self.role)

    def read_round_two(self, data: bytes) -> None:
        if self.Xp1 is None:
            raise JPakeError("read_round_one must happen first")
        off = 0
        if self.role == CLIENT:
            if len(data) < 3:
                raise JPakeError("truncated ECParameters")
            if data[0] != _TLS_NAMED_CURVE:
                raise JPakeError(f"expected a named curve, got curve_type {data[0]}")
            tls_id = int.from_bytes(data[1:3], "big")
            if tls_id != _TLS_ID_SECP256R1:
                raise JPakeError(f"peer chose curve {tls_id}, expected secp256r1")
            off = 3
        G = self.Xm1 + self.Xm2 + self.Xp1
        self.Xp, off = self._kkp_read(G, self.peer_role, data, off)
        if off != len(data):
            raise JPakeError("trailing bytes after round two")

    # -- output --

    def derive_secret(self) -> bytes:
        """The 32-byte shared session key."""
        if self.Xp is None:
            raise JPakeError("read_round_two must happen first")
        xm2_s = (self.xm2 * self.s) % _N
        K = self.xm2 * (self.Xp + (-xm2_s % _N) * self.Xp2)
        x = _affine(K).x().to_bytes(_COORD_LEN, "big")
        return hashlib.sha256(x).digest()
