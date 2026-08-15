"""The x402 wire format.

SHARED WITH WORKSTREAM B. Track C consumes a 402 and retries with proof; track B's stub
marketplace produces the 402 on ``POST /purchase``. Both sides import these models so the
format cannot drift. Agree changes before either side hardens.

Payment terms arrive from the merchant and are therefore attacker-controlled: every model here
forbids unknown fields and reuses the wire contract's capped, lowercased primitives.
"""

from __future__ import annotations

import base64
import binascii
import json

from pydantic import BaseModel, ConfigDict, Field

from trustrail.models.audit import ReferenceText
from trustrail.models.charge import Charge
from trustrail.models.money import Money
from trustrail.models.primitives import Hex32, HexAddress, ShortText, Timestamp

PAYMENT_HEADER = "X-Payment"
PAYMENT_REQUIRED_STATUS = 402
SCHEME = "x402-xsgd-avalanche"


class TermsMismatch(ValueError):
    """The merchant's terms disagree with the charge the Verifier approved.

    Always a refusal. A merchant that quotes one price and bills another, or redirects payment
    to a different address, is exactly the case the mandate exists to catch -- and the cheapest
    place to catch it is before broadcasting.
    """


class PaymentTerms(BaseModel):
    """What the merchant says it wants, returned with an HTTP 402."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: ShortText
    network: ShortText
    chain_id: int = Field(ge=1)
    asset: HexAddress
    pay_to: HexAddress
    amount: Money
    basket_hash: Hex32
    quote_id: ShortText
    expires_at: Timestamp
    nonce: ShortText

    def is_expired(self, now: Timestamp) -> bool:
        return now >= self.expires_at

    def assert_matches(self, charge: Charge) -> None:
        """Refuse terms that differ from the charge the Verifier approved.

        This duplicates checks the Verifier and the contract also make. That is deliberate:
        the cheapest place to catch a merchant changing the deal is before broadcasting.

        Currency is compared before amount on purpose -- ``Money`` refuses to compare across
        currencies, so checking it second would raise instead of reporting a mismatch.
        """
        if self.scheme != SCHEME:
            raise TermsMismatch(f"unsupported scheme {self.scheme!r}")
        if self.pay_to != charge.payout_address:
            raise TermsMismatch(
                f"terms pay to {self.pay_to}, approved payout was {charge.payout_address}"
            )
        if self.basket_hash != charge.basket_hash:
            raise TermsMismatch("terms reference a different basket than the approved charge")
        if self.quote_id != charge.quote_id:
            raise TermsMismatch("terms reference a different quote than the approved charge")
        if self.amount.currency is not charge.amount.currency:
            raise TermsMismatch(
                f"terms are in {self.amount.currency}, approved charge in "
                f"{charge.amount.currency}"
            )
        if self.amount > charge.amount:
            raise TermsMismatch(
                f"terms ask {self.amount}, more than the approved {charge.amount}"
            )


class PaymentProof(BaseModel):
    """Evidence that settlement happened, sent on the retry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: ShortText = SCHEME
    quote_id: ShortText
    basket_hash: Hex32
    mandate_id: Hex32
    amount: Money
    payer: HexAddress
    # A transaction hash is 66 characters, so this cannot be ShortText.
    reference: ReferenceText
    settled_at: Timestamp


def encode_proof(proof: PaymentProof) -> str:
    """Encode a proof for the ``X-Payment`` header (base64 of compact JSON)."""
    return base64.b64encode(proof.model_dump_json().encode("utf-8")).decode("ascii")


def decode_proof(header_value: str) -> PaymentProof:
    """Decode and validate an ``X-Payment`` header."""
    try:
        raw = base64.b64decode(header_value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("X-Payment header is not valid base64") from error
    return PaymentProof.model_validate_json(raw)


def parse_terms(body: bytes | str | dict) -> PaymentTerms:
    """Parse a 402 response body into terms."""
    if isinstance(body, dict):
        return PaymentTerms.model_validate(body)
    return PaymentTerms.model_validate(json.loads(body))
