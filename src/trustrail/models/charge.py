"""The charge: what the agent is actually asking to pay, and to whom.

Everything here is downstream of attacker-controlled input — the merchant's
listing payload chose the price, the SKU and the payout address. Nothing on
this model is trusted; the Verifier checks each field against the mandate and
the Merchant Registry.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from trustrail.models.money import Money
from trustrail.models.primitives import (
    Hex32,
    HexAddress,
    MerchantText,
    ShortText,
)


class Charge(BaseModel):
    """A settlement request bound to exactly one mandate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    charge_id: Hex32
    mandate_id: Hex32
    merchant_id: ShortText
    payout_address: HexAddress = Field(
        description=(
            "Where the merchant claims funds should go. Never used as-is: the "
            "Verifier requires it to equal the address on file in the Merchant "
            "Registry, which is what stops a scam sub-seller redirecting funds."
        )
    )
    amount: Money
    basket_hash: Hex32
    quote_id: ShortText
    sku: ShortText
    title: MerchantText
    quantity: Annotated[int, Field(ge=1, le=1000)]
