"""The KMS signing gotcha, tested without an AWS account.

CLAUDE.md calls this out as the thing most likely to eat an afternoon: KMS
returns a DER signature and no recovery id, and Ethereum needs `(r, s, v)` with
`s` in the low half of the curve. A fake KMS client lets all three of the fiddly
parts — DER parsing, EIP-2 normalisation, and finding `v` — be tested directly.
"""

from __future__ import annotations

from typing import Any

import pytest
from eth_keys import keys

from trustrail.contracts.scenarios import ScenarioBuilder
from trustrail.models.primitives import to_bytes
from trustrail.signing.crypto import recover_address, signed_by
from trustrail.signing.eip712 import mandate_digest
from trustrail.signing.kms import _CURVE_ORDER, _HALF_CURVE_ORDER, KmsSigner

PRIVATE_KEY = to_bytes("0x" + "44" * 32)


class FakeKmsClient:
    """Answers the two calls `KmsSigner` makes, the way KMS answers them.

    `high_s` forces the signature into the upper half of the curve order, which
    KMS is entitled to do and Ethereum will not accept. It is the one behaviour
    that is awkward to provoke against real KMS and easy to get wrong.
    """

    def __init__(self, private_key: bytes, *, high_s: bool = False) -> None:
        self._key = keys.PrivateKey(private_key)
        self._high_s = high_s

    def get_public_key(self, KeyId: str) -> dict[str, Any]:  # noqa: N803 - boto3 API
        # Real KMS wraps the key in SubjectPublicKeyInfo DER; only the trailing
        # 64 bytes of uncompressed point are meaningful to us.
        return {"PublicKey": b"\x30\x56\x30\x10" + b"\x04" + self._key.public_key.to_bytes()}

    def sign(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["MessageType"] == "DIGEST", "KMS must not re-hash our digest"
        signature = self._key.sign_msg_hash(kwargs["Message"])
        s = _CURVE_ORDER - signature.s if self._high_s else signature.s
        return {"Signature": _to_der(signature.r, s)}


def _to_der(r: int, s: int) -> bytes:
    body = _der_integer(r) + _der_integer(s)
    return bytes([0x30, len(body)]) + body


def _der_integer(value: int) -> bytes:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    # DER integers are signed, so a leading high bit needs a zero byte in front.
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return bytes([0x02, len(raw)]) + raw


@pytest.fixture
def digest(build: ScenarioBuilder) -> bytes:
    return to_bytes(mandate_digest(build.mandate(), build.domain))


def test_the_address_comes_from_the_kms_public_key() -> None:
    signer = KmsSigner("alias/issuer", client=FakeKmsClient(PRIVATE_KEY))

    assert signer.address == keys.PrivateKey(PRIVATE_KEY).public_key.to_address()


def test_a_kms_signature_verifies_against_the_kms_address(digest: bytes) -> None:
    signer = KmsSigner("alias/issuer", client=FakeKmsClient(PRIVATE_KEY))

    signature = signer.sign(digest)

    assert signed_by(digest, signature, signer.address)


def test_a_high_s_signature_is_normalised_and_still_verifies(digest: bytes) -> None:
    """EIP-2 rejects the high form, so we fold it before anyone sees it."""
    signer = KmsSigner(
        "alias/issuer", client=FakeKmsClient(PRIVATE_KEY, high_s=True)
    )

    signature = signer.sign(digest)

    s = int.from_bytes(to_bytes(signature)[32:64], "big")
    assert s <= _HALF_CURVE_ORDER
    assert recover_address(digest, signature) == signer.address


def test_signatures_are_the_sixty_five_byte_ethereum_shape(digest: bytes) -> None:
    signer = KmsSigner("alias/issuer", client=FakeKmsClient(PRIVATE_KEY))

    raw = to_bytes(signer.sign(digest))

    assert len(raw) == 65
    assert raw[64] in (27, 28)


def test_a_mandate_signed_via_kms_passes_the_verifier(
    build: ScenarioBuilder, digest: bytes
) -> None:
    """The end the demo actually depends on: KMS-signed mandates verify."""
    from trustrail.models.mandate import SignedMandate
    from trustrail.verifier.config import VerifierConfig
    from trustrail.verifier.service import VerifierService

    signer = KmsSigner("alias/issuer", client=FakeKmsClient(PRIVATE_KEY))
    mandate = build.mandate()
    signed = SignedMandate(
        mandate=mandate,
        digest=mandate_digest(mandate, build.domain),
        signature=signer.sign(digest),
    )
    verifier = VerifierService(VerifierConfig(issuer_address=signer.address))

    verdict = verifier.verify(build.request(signed_mandate=signed))

    assert verdict.decision.value == "PASS"
