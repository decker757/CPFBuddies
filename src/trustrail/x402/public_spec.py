"""The *public* x402 wire format, as spoken by third-party merchants.

There are two things called x402 in this repo and they are not the same shape:

- `trustrail.x402.terms` is **ours**, shared between workstreams B and C. Scheme
  `x402-xsgd-avalanche`, an `X-Payment` header, and a `PaymentProof` carrying a
  transaction hash we broadcast ourselves.
- **this module** is the public spec a merchant like the StraitsX card API
  implements. Scheme `exact`, a `PAYMENT-REQUIRED` header carrying base64 JSON,
  a `PAYMENT-SIGNATURE` header going back, and no transaction of our own —
  the merchant submits the EIP-3009 authorisation and settles it.

Keeping them in separate modules is deliberate. They serve different sides:
ours is what our stub marketplace quotes, this is what we consume when we are
the buyer. Merging them would mean one set of models trying to describe both.

Everything here arrives from a merchant and is therefore attacker-controlled.
The models forbid unknown fields, and `PaymentRequirements.assert_affordable`
exists so that a merchant cannot quote one price in its 402 and be paid another.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trustrail.models.money import Currency, Money
from trustrail.models.primitives import HexAddress, ShortText
from trustrail.signing.eip712 import Eip712Domain
from trustrail.signing.eip3009 import TransferAuthorization

REQUIREMENTS_HEADER = "PAYMENT-REQUIRED"
SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_REQUIRED_STATUS = 402

#: The only scheme and transfer method we implement.
EXACT_SCHEME = "exact"
EIP3009_METHOD = "eip3009"


class UnsupportedRequirements(ValueError):
    """The merchant wants paying in a way this client cannot do.

    Always a refusal rather than an error. A merchant asking for a scheme we do
    not implement is a fact about the merchant, and retrying will not change it.
    """


class QuotedTooMuch(ValueError):
    """The 402 asks for more than the Verifier approved.

    The cheapest place to catch a merchant changing the deal is before signing
    an authorisation, because a signed authorisation is a bearer instrument.
    """


class TokenExtra(BaseModel):
    """The token details needed to build its EIP-712 domain."""

    model_config = ConfigDict(extra="allow", frozen=True)

    asset_transfer_method: Annotated[str, Field(alias="assetTransferMethod")]
    name: ShortText
    version: ShortText


class PaymentRequirements(BaseModel):
    """One way a merchant will accept payment, from the 402 challenge."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    scheme: ShortText
    network: ShortText
    #: Minor units, as a decimal string. The merchant's own scaling.
    amount: ShortText
    asset: HexAddress
    pay_to: Annotated[HexAddress, Field(alias="payTo")]
    chain_id: Annotated[int, Field(alias="chainId", ge=1)]
    max_timeout_seconds: Annotated[int, Field(alias="maxTimeoutSeconds", ge=1)] = 300
    extra: TokenExtra

    @property
    def minor_units(self) -> int:
        return int(self.amount)

    def token_domain(self) -> Eip712Domain:
        """The EIP-712 domain of the *token*, which is what signs the transfer.

        Built from the challenge rather than from our config: the merchant is
        telling us which contract will verify the signature, and a domain we
        assumed instead would produce a signature that contract rejects.
        """
        return Eip712Domain(
            name=self.extra.name,
            version=self.extra.version,
            chain_id=self.chain_id,
            verifying_contract=self.asset,
        )

    def assert_supported(self) -> None:
        if self.scheme != EXACT_SCHEME:
            raise UnsupportedRequirements(f"unsupported x402 scheme {self.scheme!r}")
        if self.extra.asset_transfer_method != EIP3009_METHOD:
            raise UnsupportedRequirements(
                f"unsupported transfer method {self.extra.asset_transfer_method!r}"
            )

    def assert_affordable(self, approved: Money) -> None:
        """Refuse a 402 that asks for more than the Verifier let through.

        Compared in minor units against the *approved charge*, not against the
        mandate cap. The cap is what the buyer permitted at most; the charge is
        what they were told they were paying.
        """
        if approved.currency is not Currency.XSGD:
            raise QuotedTooMuch(
                f"this rail settles XSGD; the approved charge is {approved.currency}"
            )
        if self.minor_units > approved.minor_units:
            raise QuotedTooMuch(
                f"the 402 asks {self.minor_units} minor units, more than the "
                f"approved {approved.minor_units}"
            )


class PaymentRequired(BaseModel):
    """The whole 402 body: an error and the ways the merchant will take money."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    x402_version: Annotated[int, Field(alias="x402Version")] = 1
    accepts: Annotated[list[PaymentRequirements], Field(min_length=1, max_length=10)]
    error: str | None = None

    def first_supported(self) -> PaymentRequirements:
        """The first offer we can actually satisfy.

        A merchant may offer several. Picking the first supported one keeps the
        choice deterministic, which matters when the alternative is a demo that
        behaves differently on different runs.
        """
        for requirements in self.accepts:
            try:
                requirements.assert_supported()
            except UnsupportedRequirements:
                continue
            return requirements
        raise UnsupportedRequirements(
            f"none of the {len(self.accepts)} offered payment methods are supported"
        )


def parse_requirements(body: bytes | str | dict[str, Any]) -> PaymentRequired:
    """Parse a 402 response body."""
    if isinstance(body, dict):
        return PaymentRequired.model_validate(body)
    return PaymentRequired.model_validate(json.loads(body))


def decode_requirements_header(header_value: str) -> PaymentRequired:
    """Parse the base64 `PAYMENT-REQUIRED` header.

    Carried in both the header and the body. The header is authoritative here
    because it is what the spec defines; the body is a convenience.
    """
    try:
        raw = base64.b64decode(header_value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise UnsupportedRequirements(
            f"{REQUIREMENTS_HEADER} is not valid base64"
        ) from error
    return parse_requirements(raw)


class SignedAuthorization(BaseModel):
    """The payload sent back in `PAYMENT-SIGNATURE`.

    Field names are the spec's camelCase, not ours: this crosses to somebody
    else's parser, so their spelling wins.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    x402Version: int = 1
    scheme: Literal["exact"] = EXACT_SCHEME
    network: str
    payload: dict[str, Any]


def build_signature_header(
    *,
    requirements: PaymentRequirements,
    authorization: TransferAuthorization,
    signature: str,
) -> str:
    """Encode a signed authorisation for the `PAYMENT-SIGNATURE` header.

    Values are decimal strings because the spec passes uint256 as strings —
    JSON numbers cannot carry them safely once amounts get large, and a
    silently truncated value here would be a silently wrong payment.
    """
    payload = SignedAuthorization(
        network=requirements.network,
        payload={
            "signature": signature,
            "authorization": {
                "from": authorization.from_address,
                "to": authorization.to,
                "value": str(authorization.value),
                "validAfter": str(authorization.valid_after),
                "validBefore": str(authorization.valid_before),
                "nonce": authorization.nonce,
            },
        },
    )
    return base64.b64encode(
        payload.model_dump_json(by_alias=True).encode("utf-8")
    ).decode("ascii")
