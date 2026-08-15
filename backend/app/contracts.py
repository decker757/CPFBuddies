"""Shared Workstream B contracts.

These models are the integration boundary with the mandate, verifier, settlement,
and orchestration workstreams. They deliberately reject undeclared input fields.
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
    field_serializer,
    field_validator,
)

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)
]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Hash = Annotated[str, StringConstraints(pattern=r"^0x[a-f0-9]{64}$")]
Address = Annotated[str, StringConstraints(pattern=r"^0x[a-fA-F0-9]{40}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Money(StrictModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: Literal["XSGD"] = "XSGD"

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, "f")


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


class PaymentTerms(StrictModel):
    scheme: Literal["x402"] = "x402"
    network: Literal["avalanche-c-chain"] = "avalanche-c-chain"
    asset: Literal["XSGD"] = "XSGD"
    amount: Decimal
    payout_address: Address
    quote_id: Identifier
    basket_hash: Hash

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, "f")


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
    payment_proof: ShortText


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
