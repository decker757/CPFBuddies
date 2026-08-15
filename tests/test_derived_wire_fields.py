"""Derived fields that have to reach the browser.

`Verdict.failed_deterministically` decides whether the approval UI renders an
override button, and `ReviewHold.approvable` decides whether a hold may be shown
to a human at all. Both were plain properties, which meant neither appeared in
the JSON: a dashboard read `undefined`, got a falsy value, and would have
offered a button that clicks past a bad signature.

They are computed fields now — emitted, never accepted. These tests pin both
halves, because losing either one is silent. Serialisation breaking is invisible
until a demo; input being accepted would let a caller assert its own answer to
"was this a fact or a threshold".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from trustrail.models.charge import Charge
from trustrail.models.evaluation import (
    EvaluationSubject,
    EvaluatorFlags,
    EvaluatorOutput,
    SignedEvaluatorOutput,
)
from trustrail.models.money import Currency, Money
from trustrail.models.review import ReviewHold
from trustrail.models.verdict import (
    CheckKind,
    CheckResult,
    Decision,
    ReasonCode,
    Verdict,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
MANDATE_ID = "0x" + "ab" * 32
CHARGE_ID = "0x" + "cd" * 32
BASKET = "0x" + "be" * 32
PRICE = Money(currency=Currency.XSGD, amount="4.20")

DETERMINISTIC_FAIL = CheckResult(
    name="charge_within_cap",
    kind=CheckKind.DETERMINISTIC,
    decision=Decision.FAIL,
    reason=ReasonCode.CHARGE_OVER_CAP,
)
JUDGEMENT_REVIEW = CheckResult(
    name="risk_score",
    kind=CheckKind.JUDGEMENT,
    decision=Decision.REVIEW,
    reason=ReasonCode.RISK_SCORE_REVIEW_BAND,
)


def _verdict(*checks: CheckResult, decision: Decision) -> Verdict:
    return Verdict(
        decision=decision,
        reason_codes=[c.reason for c in checks if c.reason],
        checks=list(checks),
        mandate_id=MANDATE_ID,
        charge_id=CHARGE_ID,
        risk_score=5,
        evaluated_at=NOW,
        verifier_version="test",
        config_version="test",
    )


def _hold(verdict: Verdict) -> ReviewHold:
    return ReviewHold(
        charge_id=CHARGE_ID,
        mandate_id=MANDATE_ID,
        verdict=verdict,
        charge=Charge(
            charge_id=CHARGE_ID,
            mandate_id=MANDATE_ID,
            merchant_id="mrc_demo",
            payout_address="0x" + "11" * 20,
            amount=PRICE,
            basket_hash=BASKET,
            quote_id="q_01HXTEST",
            sku="TB-SOFT-2PK",
            title="Soft bristle toothbrush, 2 pack",
            quantity=1,
        ),
        evaluation=SignedEvaluatorOutput(
            evaluation=EvaluatorOutput(
                evaluator_id="evaluator-rules-v1",
                subject=EvaluationSubject(
                    mandate_id=MANDATE_ID, basket_hash=BASKET, amount=PRICE
                ),
                risk_score=5,
                flags=EvaluatorFlags(
                    intent_match=True,
                    injection_suspected=False,
                    price_far_below_market=True,
                    seller_is_new=True,
                ),
                reasons=["Unknown seller."],
            ),
            digest="0x" + "ef" * 32,
            signature="0x" + "11" * 65,
        ),
        held_at=NOW,
        deadline=NOW + timedelta(minutes=10),
    )


class TestVerdictReachesTheBrowser:
    def test_the_field_is_in_the_json(self):
        payload = json.loads(
            _verdict(DETERMINISTIC_FAIL, decision=Decision.FAIL).model_dump_json()
        )

        assert payload["failed_deterministically"] is True

    def test_a_judgement_call_is_not_a_deterministic_failure(self):
        """The REVIEW band is a threshold in config, and a human may answer it."""
        payload = json.loads(
            _verdict(JUDGEMENT_REVIEW, decision=Decision.REVIEW).model_dump_json()
        )

        assert payload["failed_deterministically"] is False

    def test_it_is_in_the_published_schema(self):
        """Guards the export mode: computed fields vanish under the default."""
        schema = Verdict.model_json_schema(mode="serialization")

        assert "failed_deterministically" in schema["properties"]

    def test_a_verdict_survives_a_round_trip(self):
        """The settlement queue serialises to JSON and validates back."""
        original = _verdict(DETERMINISTIC_FAIL, decision=Decision.FAIL)

        restored = Verdict.model_validate_json(original.model_dump_json())

        assert restored == original
        assert restored.failed_deterministically is True

    def test_a_caller_cannot_assert_its_own_answer(self):
        """Emitted, never accepted: the checks decide, not the payload."""
        payload = json.loads(
            _verdict(DETERMINISTIC_FAIL, decision=Decision.FAIL).model_dump_json()
        )
        payload["failed_deterministically"] = False

        assert Verdict.model_validate(payload).failed_deterministically is True

    def test_unknown_fields_are_still_refused(self):
        """Dropping the derived key must not have loosened the model."""
        payload = json.loads(
            _verdict(JUDGEMENT_REVIEW, decision=Decision.REVIEW).model_dump_json()
        )
        payload["surprise"] = 1

        with pytest.raises(ValueError, match="surprise"):
            Verdict.model_validate(payload)


class TestHoldApprovability:
    def test_a_judgement_hold_may_be_shown_to_a_human(self):
        hold = _hold(_verdict(JUDGEMENT_REVIEW, decision=Decision.REVIEW))

        assert json.loads(hold.model_dump_json())["approvable"] is True

    def test_a_deterministic_failure_is_never_approvable(self):
        hold = _hold(_verdict(DETERMINISTIC_FAIL, decision=Decision.FAIL))

        assert json.loads(hold.model_dump_json())["approvable"] is False

    def test_a_hold_survives_a_round_trip(self):
        original = _hold(_verdict(JUDGEMENT_REVIEW, decision=Decision.REVIEW))

        restored = ReviewHold.model_validate_json(original.model_dump_json())

        assert restored == original

    def test_a_caller_cannot_make_a_fact_approvable(self):
        hold = _hold(_verdict(DETERMINISTIC_FAIL, decision=Decision.FAIL))
        payload = json.loads(hold.model_dump_json())
        payload["approvable"] = True

        assert ReviewHold.model_validate(payload).approvable is False
