"""A charge held for a human decision.

CLAUDE.md is explicit that REVIEW needs a hold state *and* a timeout, and that we
must not build an indefinite pending queue. `deadline` is how that is enforced:
a hold can never outlive its mandate, so a REVIEW nobody answers lapses into the
same FAIL an expired mandate gets, with no sweeper process to go wrong.

The Purchase Orchestrator creates and resolves these. Workstream A owns the
shape and the deadline arithmetic.

A hold carries the whole paused purchase — the charge and the signed evidence,
not just the verdict — for two reasons. The approval surface has to render the
listing, the price and the Evaluator's reasons before a human can sensibly
decide, and approving re-enters the Verifier, which needs the same evidence it
judged the first time.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trustrail.models.charge import Charge
from trustrail.models.evaluation import SignedEvaluatorOutput
from trustrail.models.primitives import Hex32, ShortText, Timestamp
from trustrail.models.verdict import Verdict


class ReviewOutcome(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    KILLED = "KILLED"
    EXPIRED = "EXPIRED"


class HumanApproval(BaseModel):
    """A named person accepting the Evaluator's findings on a held charge.

    This exists so that settling a REVIEW is never silent about *why* it
    settled. The verdict that travels to the queue still says REVIEW, with the
    reasons that held it; this says who overrode them. Fabricating a PASS
    instead would put a lie in the audit trail at exactly the point the trail
    matters most.

    It can only ever answer a judgement call. Nothing here can rescue a
    deterministic failure — see `ReviewHold.approvable`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    charge_id: Hex32
    approved_by: ShortText
    approved_at: Timestamp


class ReviewHold(BaseModel):
    """A paused charge, its evidence, and the moment it stops waiting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    charge_id: Hex32
    mandate_id: Hex32
    verdict: Verdict
    charge: Charge = Field(
        description="The charge being held, so the approval surface can show "
        "the buyer what they are approving."
    )
    evaluation: SignedEvaluatorOutput = Field(
        description="The evidence that produced the verdict. Re-verification "
        "on approval judges this same evidence, not a fresh evaluation."
    )
    outcome: ReviewOutcome = ReviewOutcome.PENDING
    held_at: Timestamp
    deadline: Timestamp
    resolved_by: ShortText | None = None

    @model_validator(mode="after")
    def _parts_must_describe_one_purchase(self) -> Self:
        """The ids, the charge and the verdict must all be about the same thing.

        A hold assembled from one charge and another charge's verdict would put
        the wrong price and the wrong reasons in front of the human approving
        it, and approval re-verifies whatever the hold carries.
        """
        if self.charge.charge_id != self.charge_id:
            raise ValueError("hold carries a charge with a different charge id")
        if self.charge.mandate_id != self.mandate_id:
            raise ValueError("hold carries a charge against a different mandate")
        if self.verdict.charge_id != self.charge_id:
            raise ValueError("hold carries a verdict about a different charge")
        return self

    @property
    def approvable(self) -> bool:
        """Whether a human is even allowed to be asked about this.

        A deterministic failure is a fact — a bad signature, an over-cap amount,
        an unregistered merchant. Offering a person a button for one of those
        would teach them to click through everything, which is precisely what
        would destroy the claim the rail makes. The approval UI keys off this.
        """
        return not self.verdict.failed_deterministically

    @staticmethod
    def deadline_for(
        *, now: datetime, mandate_expires_at: datetime, review_window: timedelta
    ) -> datetime:
        """The earlier of the review window and the mandate's own expiry.

        Taking the minimum is the whole guarantee: a human cannot be given more
        time to approve than the mandate itself has left to live.
        """
        return min(now + review_window, mandate_expires_at)

    def resolve(self, *, outcome: ReviewOutcome, by: str) -> Self:
        return self.model_copy(update={"outcome": outcome, "resolved_by": by})
