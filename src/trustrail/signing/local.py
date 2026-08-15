"""A signer backed by a local private key.

For tests, for the offline demo path, and for the fallback CLAUDE.md calls for
if the KMS recovery-id work stalls: same `Signer` interface as `KmsSigner`, so
swapping between them is a wiring change.

This must never hold the production issuer key. In production the key lives in
KMS and never leaves it — that is the whole point of `KmsSigner`.
"""

from __future__ import annotations

import secrets

from trustrail.models.primitives import to_bytes
from trustrail.signing.crypto import address_of, sign_digest


class LocalSigner:
    """Signs with an in-process secp256k1 key."""

    def __init__(self, private_key: bytes) -> None:
        if len(private_key) != 32:
            raise ValueError("a secp256k1 private key is 32 bytes")
        self._private_key = private_key
        self._address = address_of(private_key)

    @classmethod
    def generate(cls) -> LocalSigner:
        """A fresh random key. Tests and local runs only."""
        return cls(secrets.token_bytes(32))

    @classmethod
    def from_hex(cls, private_key: str) -> LocalSigner:
        return cls(to_bytes(private_key))

    @property
    def address(self) -> str:
        return self._address

    def sign(self, digest: bytes) -> str:
        return sign_digest(self._private_key, digest)
