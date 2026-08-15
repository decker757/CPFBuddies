"""What the Browser Agent came back with.

This is a `Charge` before it has a charge id or a mandate to belong to: the
product, the price, and the merchant's own claims about itself. The Purchase
Orchestrator turns it into a `Charge` once it knows which mandate is paying.

Everything in here is attacker-controlled. The Browser Agent read it off a
merchant's `/listings` response, and CLAUDE.md is explicit that the Browser
Agent is assumed compromisable and that nothing downstream trusts its output.
The point of naming the shape is that the untrusted values arrive somewhere
they can be checked, rather than being spread through a call signature.

`payout_address` in particular is the address the *listing claimed*. It is
carried through to the charge unaltered and on purpose, so the Verifier can
compare it against the Merchant Registry and reject a mismatch. Substituting
the registered address here would make that check compare a value against
itself and quietly delete the sub-seller protection.
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


class PurchaseCandidate(BaseModel):
    """A product the Browser Agent selected, and the quote it was selected from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    merchant_id: ShortText
    payout_address: HexAddress = Field(
        description="The payout address this listing claimed. Never trusted: "
        "the Verifier requires it to equal the Merchant Registry's record."
    )
    amount: Money
    basket_hash: Hex32
    quote_id: ShortText
    sku: ShortText
    title: MerchantText
    quantity: Annotated[int, Field(ge=1, le=1000)] = 1
