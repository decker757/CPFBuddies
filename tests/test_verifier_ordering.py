"""The invariants that make the verdict model mean what we say it means.

Two claims are made repeatedly about this system, and they are only true if this
file passes:

- a deterministic failure can never be overridden by a human;
- a risk score can never rescue a charge that failed a deterministic check.
"""

from __future__ import annotations

from datetime import timedelta

from trustrail.contracts.scenarios import DEMO_NOW, ScenarioBuilder
from trustrail.models.evaluation import EvaluatorFlags
from trustrail.models.verdict import CheckKind, Decision, ReasonCode
from trustrail.verifier.checks import DETERMINISTIC_CHECKS, Rejection
from trustrail.verifier.service import VerifierService, _record


def test_expired_mandate_fails_even_with_a_perfect_risk_score(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    request = build.request(now=DEMO_NOW + timedelta(hours=1))

    verdict = verifier.verify(request)

    assert verdict.decision is Decision.FAIL
    assert verdict.reason_codes == [ReasonCode.MANDATE_EXPIRED]


def test_a_deterministic_failure_stops_the_pipeline(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    """No judgement check runs once a fact has already settled it."""
    request = build.request(
        now=DEMO_NOW + timedelta(hours=1),
        evaluation=build.sign_evaluation(
            build.evaluation(build.charge(), risk_score=10)
        ),
    )

    verdict = verifier.verify(request)

    assert verdict.reason_codes == [ReasonCode.MANDATE_EXPIRED]
    assert all(check.kind is CheckKind.DETERMINISTIC for check in verdict.checks)


def test_no_checks_run_after_the_first_deterministic_failure(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    """The trace is a record of what was consulted, not of what exists."""
    request = build.request(merchant=None)

    verdict = verifier.verify(request)

    assert verdict.checks[-1].reason is ReasonCode.MERCHANT_NOT_REGISTERED
    assert len(verdict.checks) < len(DETERMINISTIC_CHECKS)


def test_a_deterministic_check_asking_for_review_still_fails() -> None:
    """The one place the 'facts cannot be overridden' rule is enforced.

    If a future check author writes `Decision.REVIEW` into a deterministic
    check, the pipeline overrules them rather than quietly opening an override
    path around a cryptographic fact.
    """
    softened = Rejection(
        ReasonCode.MANDATE_EXPIRED, "expired", decision=Decision.REVIEW
    )

    result = _record(_named_check, CheckKind.DETERMINISTIC, softened)

    assert result.decision is Decision.FAIL


def test_a_judgement_check_keeps_the_decision_it_asked_for() -> None:
    asked_for_review = Rejection(
        ReasonCode.INJECTION_SUSPECTED, "suspicious", decision=Decision.REVIEW
    )

    result = _record(_named_check, CheckKind.JUDGEMENT, asked_for_review)

    assert result.decision is Decision.REVIEW


def test_judgement_checks_collect_every_reason(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    """A human deciding a REVIEW should see all of it, not just the first."""
    everything_wrong = build.evaluation(
        build.charge(),
        risk_score=6,
        flags=EvaluatorFlags(
            intent_match=False,
            injection_suspected=True,
            price_far_below_market=True,
            seller_is_new=True,
        ),
    )

    verdict = verifier.verify(
        build.request(evaluation=build.sign_evaluation(everything_wrong))
    )

    assert verdict.decision is Decision.REVIEW
    assert set(verdict.reason_codes) == {
        ReasonCode.RISK_SCORE_REVIEW_BAND,
        ReasonCode.INTENT_MISMATCH_SUSPECTED,
        ReasonCode.INJECTION_SUSPECTED,
        ReasonCode.SUSPICIOUS_SELLER_PRICING,
    }


def test_worst_decision_wins_among_judgement_checks(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    """One critical score outranks any number of merely-suspicious flags."""
    critical_and_suspicious = build.evaluation(
        build.charge(),
        risk_score=9,
        flags=EvaluatorFlags(
            intent_match=False,
            injection_suspected=True,
            price_far_below_market=False,
            seller_is_new=False,
        ),
    )

    verdict = verifier.verify(
        build.request(evaluation=build.sign_evaluation(critical_and_suspicious))
    )

    assert verdict.decision is Decision.FAIL


def test_decision_severity_ordering() -> None:
    assert Decision.worst([]) is Decision.PASS
    assert Decision.worst([Decision.PASS, Decision.REVIEW]) is Decision.REVIEW
    assert Decision.worst([Decision.REVIEW, Decision.FAIL]) is Decision.FAIL
    assert Decision.worst([Decision.FAIL, Decision.PASS]) is Decision.FAIL


def _named_check(_: object) -> None:  # pragma: no cover - only its name is used
    return None
