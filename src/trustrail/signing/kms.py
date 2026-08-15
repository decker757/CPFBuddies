"""A signer whose private key lives in KMS and never leaves it.

KMS will happily sign a secp256k1 digest, but it returns a DER-encoded ECDSA
signature — and Ethereum needs `(r, s, v)`, where `v` is the recovery id that
lets a verifier reconstruct the public key from the signature alone. KMS does
not give you `v`, because `v` is not part of ECDSA; it is an Ethereum
convenience. Three things therefore have to happen here:

1. **Parse the DER.** Two INTEGERs in a SEQUENCE, each possibly carrying a
   leading zero byte to keep it positive.
2. **Normalise `s` to the low half of the curve order.** Every ECDSA signature
   has a twin with `s' = n - s` that is equally valid; EIP-2 rejects the high
   one, so Ethereum tooling and contracts only accept the low form.
3. **Recover `v` by trying it.** There are two candidates, 0 and 1. Recover the
   address with each and keep the one that matches the key's own address.

CLAUDE.md timeboxes this to two hours and names the fallback: a key in Parameter
Store behind `LocalSigner`. Because both implement the same `Signer` port, that
fallback is a wiring change, not a rewrite.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

import boto3
from eth_keys import keys
from eth_utils import keccak

from trustrail.signing.crypto import recover_address

#: Order of the secp256k1 group. EIP-2 requires s <= n/2.
_CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_HALF_CURVE_ORDER = _CURVE_ORDER // 2

_KMS_SIGNING_ALGORITHM = "ECDSA_SHA_256"
_DER_SEQUENCE = 0x30
_DER_INTEGER = 0x02
_V_OFFSET = 27


class KmsSigner:
    """Signs mandate digests with an asymmetric KMS key.

    The key must be `ECC_SECG_P256K1` with usage `SIGN_VERIFY`. Nothing in this
    class can export the key; the only capability it has is "sign this digest",
    which is exactly the authority the Mandate Service needs and no more.
    """

    def __init__(self, key_id: str, *, client: Any = None) -> None:
        self._key_id = key_id
        self._client = client or boto3.client("kms")

    @cached_property
    def address(self) -> str:
        """The address derived from the KMS public key, fetched once."""
        return self.public_key.to_address()

    @cached_property
    def public_key(self) -> keys.PublicKey:
        """The KMS public key, unwrapped from its SubjectPublicKeyInfo DER.

        The uncompressed secp256k1 point is the last 65 bytes, and its leading
        0x04 marker is not part of the coordinates.
        """
        response = self._client.get_public_key(KeyId=self._key_id)
        return keys.PublicKey(response["PublicKey"][-64:])

    def sign(self, digest: bytes) -> str:
        """Sign a 32-byte digest, returning `0x` + r || s || v."""
        response = self._client.sign(
            KeyId=self._key_id,
            Message=digest,
            # DIGEST, not RAW: we hand KMS the keccak hash we already computed,
            # not the message. Letting KMS hash it would apply SHA-256 and
            # produce a signature over the wrong thing entirely.
            MessageType="DIGEST",
            SigningAlgorithm=_KMS_SIGNING_ALGORITHM,
        )
        r, s = _parse_der_signature(response["Signature"])
        return _pack(r, _normalise_s(s), digest, self.address)


def _parse_der_signature(der: bytes) -> tuple[int, int]:
    """Extract (r, s) from `SEQUENCE { INTEGER r, INTEGER s }`."""
    if not der or der[0] != _DER_SEQUENCE:
        raise ValueError("KMS signature is not a DER SEQUENCE")
    body = der[2:]  # skip the sequence tag and its length byte
    r, remainder = _read_der_integer(body)
    s, _ = _read_der_integer(remainder)
    return r, s


def _read_der_integer(data: bytes) -> tuple[int, bytes]:
    if not data or data[0] != _DER_INTEGER:
        raise ValueError("expected a DER INTEGER in the KMS signature")
    length = data[1]
    # A leading zero byte is DER's way of keeping a high bit from reading as a
    # negative number; int.from_bytes does not need it.
    return int.from_bytes(data[2 : 2 + length], "big"), data[2 + length :]


def _normalise_s(s: int) -> int:
    """Fold `s` into the low half of the curve order, as EIP-2 requires."""
    return s if s <= _HALF_CURVE_ORDER else _CURVE_ORDER - s


def _pack(r: int, s: int, digest: bytes, expected_address: str) -> str:
    """Find the recovery id that reproduces our own address, and pack it in."""
    for candidate in (0, 1):
        signature = _to_hex(r, s, candidate)
        if recover_address(digest, signature) == expected_address:
            return signature
    raise ValueError(
        "no recovery id reproduced the signing address; the KMS key and the "
        "digest do not correspond"
    )


def _to_hex(r: int, s: int, v: int) -> str:
    packed = (
        r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([v + _V_OFFSET])
    )
    return "0x" + packed.hex()


def address_from_public_key(public_key_der: bytes) -> str:
    """The Ethereum address for a KMS `GetPublicKey` DER blob.

    Useful for the deploy script: it prints the address to configure the
    Verifier with, without minting anything.
    """
    return "0x" + keccak(public_key_der[-64:])[-20:].hex()
