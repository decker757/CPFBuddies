"""secp256k1 primitives, in the shape Avalanche expects.

A signature on the wire is 65 bytes, `r || s || v`, with `v` in {27, 28} — the
Ethereum convention, so the same bytes a contract would accept are the bytes we
store and verify.

Verification here is pure computation: recover the signer address from the
digest and compare it to an address we already hold. No key lookup, no network
call, which is what lets the Verifier stay a pure function.
"""

from __future__ import annotations

from eth_keys import keys
from eth_keys.exceptions import BadSignature
from eth_utils import keccak

from trustrail.models.primitives import to_bytes

SIGNATURE_BYTES = 65
_V_OFFSET = 27


def hash_bytes(data: bytes) -> str:
    """keccak256, returned as a 0x-prefixed lowercase hash."""
    return "0x" + keccak(data).hex()


def sign_digest(private_key: bytes, digest: bytes) -> str:
    """Sign a 32-byte digest, returning `0x` + r || s || v with v in {27, 28}."""
    signature = keys.PrivateKey(private_key).sign_msg_hash(digest)
    packed = (
        signature.r.to_bytes(32, "big")
        + signature.s.to_bytes(32, "big")
        + bytes([signature.v + _V_OFFSET])
    )
    return "0x" + packed.hex()


def address_of(private_key: bytes) -> str:
    """The lowercase address that `sign_digest` will produce signatures for."""
    return keys.PrivateKey(private_key).public_key.to_address()


def recover_address(digest: bytes, signature: str) -> str | None:
    """Recover the signing address, or None if the signature is unusable.

    Returning None rather than raising keeps the caller honest: a malformed
    signature and a wrong signer are the same outcome — the charge does not
    settle — and the Verifier should not have to wrap this in a try block.
    """
    raw = to_bytes(signature)
    if len(raw) != SIGNATURE_BYTES:
        return None
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64] - _V_OFFSET if raw[64] >= _V_OFFSET else raw[64]
    if v not in (0, 1):
        return None
    try:
        signature = keys.Signature(vrs=(v, r, s))
        return signature.recover_public_key_from_msg_hash(digest).to_address()
    except (BadSignature, ValueError):
        return None


def signed_by(digest: bytes, signature: str, expected_address: str) -> bool:
    """True when `signature` over `digest` was produced by `expected_address`."""
    return recover_address(digest, signature) == expected_address.lower()
