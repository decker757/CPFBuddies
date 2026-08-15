"""HTTP surface for the Purchase Orchestrator.

Three surfaces, matching CLAUDE.md's frontend: the chat UI posts an intent to
`POST /purchases`, the REVIEW approval surface reads `GET /reviews` and answers
with approve or kill, and the dashboard renders the `Verdict` that comes back
from any of them.

`PurchaseResponse.verdict.failed_deterministically` is the field the approval
UI keys off. When it is true there must be no override button: clicking past a
bad signature or an over-cap charge is precisely what would destroy the claim
this system makes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from trustrail.models.charge import Charge
from trustrail.models.mandate import IntentText, MandateRecord
from trustrail.models.money import Money
from trustrail.models.primitives import Hex32, HexAddress, ShortText
from trustrail.models.review import ReviewHold
from trustrail.models.verdict import Verdict
from trustrail.orchestrator.ports import NoCandidate
from trustrail.orchestrator.purchase import (
    PurchaseOrchestrator,
    PurchaseOutcome,
    ReviewNotFound,
    ReviewNotPending,
)

#: Mandate lifetimes, matching the Mandate Service's own bounds.
TtlSeconds = Annotated[int, Field(ge=60, le=86_400)]


class PurchaseRequest(BaseModel):
    """A buyer's intent, in their own words, with the budget they approved."""

    model_config = ConfigDict(extra="forbid")

    principal: HexAddress
    agent_id: ShortText
    intent: IntentText
    max_amount: Money
    ttl_seconds: TtlSeconds = 600


class ResolveReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ShortText = Field(
        description="Who decided. Lands in the audit trail as the actor on the "
        "binding or the revocation, so an approval is always attributable."
    )


class PurchaseResponse(BaseModel):
    """What happened, with enough detail for the dashboard to render it."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    charge: Charge
    mandate: MandateRecord
    hold: ReviewHold | None = None
    queued_message_id: str | None = Field(
        default=None,
        description="Set once the charge is on the settlement queue. None "
        "means nothing will move: a FAIL, or a REVIEW still waiting.",
    )

    @classmethod
    def of(cls, outcome: PurchaseOutcome) -> PurchaseResponse:
        return cls(
            verdict=outcome.verdict,
            charge=outcome.charge,
            mandate=outcome.mandate,
            hold=outcome.hold,
            queued_message_id=outcome.queued_message_id,
        )


def build_router(orchestrator: PurchaseOrchestrator) -> APIRouter:
    router = APIRouter(tags=["purchases"])

    @router.post("/purchases", status_code=status.HTTP_200_OK)
    async def purchase(request: PurchaseRequest) -> PurchaseResponse:
        """Run one purchase intent end to end and report the verdict.

        Always 200, whatever the verdict. A FAIL is a decision this system made
        correctly, not a failed request — and the reason codes are the payload
        the dashboard exists to show.
        """
        try:
            outcome = await orchestrator.purchase(
                principal=request.principal,
                agent_id=request.agent_id,
                intent=request.intent,
                max_amount=request.max_amount,
                ttl=timedelta(seconds=request.ttl_seconds),
            )
        except NoCandidate as error:
            # Not a 500: nothing broke. Every merchant answered and none of
            # them offered something this mandate could buy — most often a cap
            # below the cheapest listing. The reason is carried through
            # because it is the difference between "your budget was too low"
            # and "the pinned demo SKU is not for sale", and a demo operator
            # reading a red box needs to know which.
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"no merchant offered a candidate within this mandate: {error}",
            ) from error
        return PurchaseResponse.of(outcome)

    @router.get("/reviews")
    def pending_reviews() -> list[ReviewHold]:
        """Charges waiting on a human. Backs the REVIEW approval surface."""
        return orchestrator.pending_reviews()

    @router.get("/reviews/{charge_id}")
    def get_review(charge_id: Hex32) -> ReviewHold:
        hold = orchestrator.get_review(charge_id)
        if hold is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such held charge")
        return hold

    @router.post("/reviews/{charge_id}/approve")
    def approve(charge_id: Hex32, request: ResolveReviewRequest) -> PurchaseResponse:
        """Accept the Evaluator's findings; bind, re-verify, and settle."""
        with _hold_errors():
            outcome = orchestrator.approve_review(
                charge_id, approved_by=request.actor
            )
        return PurchaseResponse.of(outcome)

    @router.post("/reviews/{charge_id}/kill")
    def kill(charge_id: Hex32, request: ResolveReviewRequest) -> PurchaseResponse:
        """Reject a held charge and revoke the mandate behind it."""
        with _hold_errors():
            outcome = orchestrator.kill_review(charge_id, killed_by=request.actor)
        return PurchaseResponse.of(outcome)

    return router


@contextmanager
def _hold_errors() -> Iterator[None]:
    """Map the two ways resolving a hold can refuse onto status codes.

    404 means there is no such hold; 409 means there is one but it can no
    longer be answered — already resolved, or past its deadline. Both are
    ordinary outcomes of a human being slower than a ten-minute window, so
    neither is a 500.
    """
    try:
        yield
    except ReviewNotFound as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ReviewNotPending as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
