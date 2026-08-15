"""Workstream B's own shapes: listings, quotes, receipts and agent findings.

Anything that crosses into another workstream uses that workstream's definition rather than a
local copy -- `Money`, addresses and hashes come from A's wire contract, and the 402 payment
terms come from C's x402 module. B is a separate deployable because the agents are untrusted,
but a separate deployable is not a reason to redefine the vocabulary.

Models here reject undeclared input fields: the listing payloads are attacker-controlled.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from trustrail.models.audit import ReferenceText
from trustrail.models.money import Currency, Money
from trustrail.models.primitives import Hex32, HexAddress
from trustrail.x402.terms import PaymentTerms

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)
]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
#: Aliases onto the wire contract. A lowercases both at parse time, so equality comparisons
#: downstream -- payout address binding, basket binding -- need no case handling.
Hash = Hex32
Address = HexAddress


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def xsgd(amount: Decimal | str) -> Money:
    """XSGD money, spelled once. Everything B prices is in XSGD by track rule."""
    return Money(currency=Currency.XSGD, amount=str(amount))


class Merchant(StrictModel):
    id: Identifier
    address: Address
    name: ShortText


class Listing(StrictModel):
    sku: Identifier
    title: ShortText
    description: Description
    price: Money
    availability: Literal["in_stock", "out_of_stock"]
    seller_id: Identifier
    seller_account_age_days: int = Field(ge=0, le=36_500)
    seller_rating_count: int = Field(ge=0, le=1_000_000_000)
    seller_rating: float | None = Field(default=None, ge=0, le=5)


class ListingsResponse(StrictModel):
    quote_id: Identifier
    expires_at: datetime
    merchant: Merchant
    items: list[Listing] = Field(max_length=100)
    basket_hash: Hash

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value


class PurchaseRequest(StrictModel):
    sku: Identifier
    quantity: int = Field(ge=1, le=100)
    quote_id: Identifier
    mandate_credential: dict[str, Any]
    signed_request: ShortText


class PaymentRequired(StrictModel):
    error: Literal["payment_required"] = "payment_required"
    payment_terms: PaymentTerms


class MarketplaceErrorResponse(StrictModel):
    detail: Literal[
        "unknown_quote",
        "expired_quote",
        "sku_not_in_quote",
        "out_of_stock",
        "quote_already_consumed",
        "quote_integrity_failed",
    ]


class PurchaseReceipt(StrictModel):
    status: Literal["settled"] = "settled"
    quote_id: Identifier
    sku: Identifier
    quantity: int
    amount: Money
    basket_hash: Hash
    #: The rail's reference for the settlement -- a transaction hash on the onchain
    #: rail. Wider than ShortText because a 0x-prefixed 32-byte hash is 66 characters.
    payment_proof: ReferenceText


class CandidateSelection(StrictModel):
    quote_id: Identifier
    expires_at: datetime
    merchant: Merchant
    listing: Listing
    basket_hash: Hash


class RiskReasonCode(StrEnum):
    INTENT_MISMATCH = "INTENT_MISMATCH"
    PRICE_OVER_CAP = "PRICE_OVER_CAP"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    PRODUCT_SUBSTITUTION = "PRODUCT_SUBSTITUTION"
    SUSPICIOUSLY_LOW_PRICE = "SUSPICIOUSLY_LOW_PRICE"
    NEW_OR_UNRATED_SELLER = "NEW_OR_UNRATED_SELLER"
    EVALUATOR_UNAVAILABLE = "EVALUATOR_UNAVAILABLE"


class ModelAssessment(StrictModel):
    intent_match: bool
    injection_detected: bool
    substitution_suspected: bool
    risk_score: int = Field(ge=1, le=10)
    reason_codes: list[Literal["INTENT_MISMATCH", "PROMPT_INJECTION", "PRODUCT_SUBSTITUTION"]] = (
        Field(max_length=3)
    )


class RiskReason(StrictModel):
    code: RiskReasonCode
    detail: ShortText


class EvaluatorOutput(StrictModel):
    evaluator_id: Identifier = "evaluator-v1"
    risk_score: int = Field(ge=1, le=10)
    intent_match: bool
    price_within_cap: bool
    injection_detected: bool
    reasons: list[RiskReason] = Field(max_length=10)
