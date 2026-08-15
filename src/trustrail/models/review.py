"""A charge held for a human decision.

CLAUDE.md is explicit that REVIEW needs a hold state *and* a timeout, and that we
must not build an indefinite pending queue. `deadline` is how that is enforced:
a hold can never outlive its mandate, so a REVIEW nobody answers lapses into the
same FAIL an expired mandate gets, with no sweeper process to go wrong.

The Purchase Orchestrator (workstream D) creates and resolves these. Workstream A
owns the shape and the deadline arithmetic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict

from trustrail.models.primitives import Hex32, ShortText, Timestamp
from trustrail.models.verdict import Verdict


class ReviewOutcome(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    KILLED = "KILLED"
    EXPIRED = "EXPIRED"


class ReviewHold(BaseModel):
    """A paused charge, its evidence, and the moment it stops waiting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    charge_id: Hex32
    mandate_id: Hex32
    verdict: Verdict
    outcome: ReviewOutcome = ReviewOutcome.PENDING
    held_at: Timestamp
    deadline: Timestamp
    resolved_by: ShortText | None = None

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
