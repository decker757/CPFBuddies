"""The mandate: a signed credential scoped to one purchase intent.

A mandate is minted *before* a product is chosen, so `merchant_address` and
`basket_hash` are empty at mint. The human approved a budget and an intent, not
a SKU. Those two fields are filled in later by `MandateService.bind`, which
re-signs — so a bound mandate has a different digest from the one it was minted
with, and tampering with either field breaks the signature.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from trustrail.models.money import Money
from trustrail.models.primitives import (
    Hex32,
    HexAddress,
    HexSignature,
    ShortText,
    Timestamp,
)

#: The buyer's own words ("toothbrush under $5"). Capped: the intent is shown
#: to a human in the approval UI and fed to the Evaluator as untrusted context.
IntentText = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class MandateStatus(StrEnum):
    """Lifecycle of a mandate. Only MINTED and BOUND can still spend."""

    MINTED = "MINTED"
    BOUND = "BOUND"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"


class Mandate(BaseModel):
    """The struct that gets hashed and signed. Field order matches EIP-712."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mandate_id: Hex32
    principal: HexAddress
    agent_id: ShortText
    max_amount: Money
    expires_at: Timestamp
    intent: IntentText
    merchant_address: HexAddress | None = None
    basket_hash: Hex32 | None = None
    nonce: Hex32

    @property
    def is_bound(self) -> bool:
        """True once a specific merchant and basket have been committed to."""
        return self.merchant_address is not None and self.basket_hash is not None


class SignedMandate(BaseModel):
    """A mandate plus the issuer's signature over its EIP-712 digest.

    `digest` is carried for the audit trail and for track C's `spend()` call,
    but it is never trusted: the Verifier recomputes it from `mandate` and
    fails the charge if the two disagree.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mandate: Mandate
    digest: Hex32
    signature: HexSignature


class MandateState(BaseModel):
    """Storage-side facts the Verifier cannot derive from the signed struct.

    Passed *into* the Verifier rather than looked up by it — that is what keeps
    the Verifier a pure function with no network calls.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: MandateStatus
    nonce_claimed_by: Hex32 | None = Field(
        default=None,
        description=(
            "The mandate id that first claimed this nonce. If it is some other "
            "mandate, the nonce has been reused and the charge is a replay."
        ),
    )


class MandateBinding(BaseModel):
    """The only two fields a binding may fill in.

    Expressing it as a type rather than as two loose arguments is what makes
    "approving a REVIEW cannot widen the mandate" structural: there is no field
    here for the cap or the expiry, so no binding can touch them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    merchant_address: HexAddress
    basket_hash: Hex32


class MandateRecord(BaseModel):
    """A mandate as it is stored: the signed struct plus its lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signed: SignedMandate
    status: MandateStatus
    created_at: Timestamp
    updated_at: Timestamp

    @property
    def mandate_id(self) -> str:
        return self.signed.mandate.mandate_id

    @property
    def principal(self) -> str:
        return self.signed.mandate.principal

    def evolve(
        self,
        *,
        status: MandateStatus,
        at: Timestamp,
        signed: SignedMandate | None = None,
    ) -> MandateRecord:
        """A copy at the next lifecycle step. Records are never mutated."""
        return self.model_copy(
            update={
                "signed": signed or self.signed,
                "status": status,
                "updated_at": at,
            }
        )
