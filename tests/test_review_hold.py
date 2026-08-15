"""Review deadlines.

CLAUDE.md is explicit: REVIEW needs a hold state and a timeout, and we must not
build an indefinite pending queue. All of that reduces to one `min()`, which is
worth testing precisely because it is easy to get backwards.
"""

from __future__ import annotations

from datetime import UTC, timedelta

from trustrail.contracts.scenarios import DEMO_NOW
from trustrail.models.review import ReviewHold, ReviewOutcome
from trustrail.verifier.config import VerifierConfig


def test_a_hold_never_outlives_its_mandate() -> None:
    """A human cannot be given longer to approve than the mandate has to live."""
    mandate_expiry = DEMO_NOW + timedelta(minutes=2)

    deadline = ReviewHold.deadline_for(
        now=DEMO_NOW,
        mandate_expires_at=mandate_expiry,
        review_window=timedelta(minutes=10),
    )

    assert deadline == mandate_expiry


def test_a_short_review_window_wins_over_a_long_mandate() -> None:
    deadline = ReviewHold.deadline_for(
        now=DEMO_NOW,
        mandate_expires_at=DEMO_NOW + timedelta(hours=1),
        review_window=timedelta(minutes=10),
    )

    assert deadline == DEMO_NOW + timedelta(minutes=10)


def test_the_review_window_is_configuration(config: VerifierConfig) -> None:
    deadline = ReviewHold.deadline_for(
        now=DEMO_NOW,
        mandate_expires_at=DEMO_NOW + timedelta(days=1),
        review_window=timedelta(seconds=config.review_hold_seconds),
    )

    assert deadline == DEMO_NOW + timedelta(seconds=config.review_hold_seconds)


def test_a_hold_starts_pending_and_records_who_resolved_it(config, verifier, build):
    verdict = verifier.verify(build.request())
    hold = ReviewHold(
        charge_id=build.charge().charge_id,
        mandate_id=verdict.mandate_id,
        verdict=verdict,
        held_at=DEMO_NOW,
        deadline=DEMO_NOW + timedelta(seconds=config.review_hold_seconds),
    )

    resolved = hold.resolve(outcome=ReviewOutcome.KILLED, by="ernest")

    assert hold.outcome is ReviewOutcome.PENDING
    assert resolved.outcome is ReviewOutcome.KILLED
    assert resolved.resolved_by == "ernest"


def test_the_demo_clock_is_timezone_aware() -> None:
    """Naive datetimes would make every expiry comparison a coin flip."""
    assert DEMO_NOW.tzinfo is UTC
