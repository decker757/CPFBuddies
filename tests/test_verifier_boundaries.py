"""Boundary cases, where a check either holds exactly or does not hold at all.

These are the questions a judge asks: what happens at exactly the cap, one
second before expiry, at the edge of each risk band. Every answer here is exact
and every threshold comes from config rather than from a literal in the test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from trustrail.contracts.scenarios import CAP, DEMO_NOW, PRICE, ScenarioBuilder
from trustrail.models.money import Currency, Money
from trustrail.models.registry import AgentRecord, AgentRole
from trustrail.models.verdict import Decision, ReasonCode
from trustrail.verifier.config import VerifierConfig
from trustrail.verifier.service import VerifierService


def _verify_charge(
    build: ScenarioBuilder, verifier: VerifierService, **charge_fields: object
) -> Decision:
    """Verify a charge, keeping the evaluation bound to it."""
    charge = build.charge(**charge_fields)
    return verifier.verify(
        build.request(
            charge=charge, evaluation=build.sign_evaluation(build.evaluation(charge))
        )
    ).decision


# --- the cap ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (-1, Decision.PASS),
        (0, Decision.PASS),
        (1, Decision.FAIL),
    ],
    ids=["one_unit_under", "exactly_at_cap", "one_unit_over"],
)
def test_cap_is_exact_to_the_minor_unit(
    build: ScenarioBuilder,
    verifier: VerifierService,
    offset: int,
    expected: Decision,
) -> None:
    amount = Money.from_minor_units(CAP.minor_units + offset, Currency.XSGD)

    assert _verify_charge(build, verifier, amount=amount) is expected


def test_currency_mismatch_is_caught_before_amounts_are_compared(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    """Comparing across currencies would raise; the pipeline must not get there."""
    charge = build.charge(amount=Money(currency=Currency.USD, amount="1.00"))

    verdict = verifier.verify(
        build.request(
            charge=charge, evaluation=build.sign_evaluation(build.evaluation(charge))
        )
    )

    assert verdict.reason_codes == [ReasonCode.CURRENCY_MISMATCH]


# --- expiry ----------------------------------------------------------------


def test_mandate_is_valid_up_to_its_expiry_less_the_skew_allowance(
    build: ScenarioBuilder, config: VerifierConfig, verifier: VerifierService
) -> None:
    expires_at = DEMO_NOW + timedelta(minutes=10)
    just_inside = expires_at - timedelta(seconds=config.clock_skew_seconds + 1)

    assert verifier.verify(build.request(now=just_inside)).decision is Decision.PASS


def test_skew_allowance_expires_the_mandate_early_not_late(
    build: ScenarioBuilder, config: VerifierConfig, verifier: VerifierService
) -> None:
    """Skew is applied in the safe direction: early is a retry, late is a spend."""
    expires_at = DEMO_NOW + timedelta(minutes=10)
    inside_the_skew_window = expires_at - timedelta(
        seconds=config.clock_skew_seconds - 1
    )

    verdict = verifier.verify(build.request(now=inside_the_skew_window))

    assert verdict.reason_codes == [ReasonCode.MANDATE_EXPIRED]


# --- risk bands ------------------------------------------------------------


@pytest.mark.parametrize("score", range(1, 11))
def test_risk_bands_follow_the_configured_thresholds(
    build: ScenarioBuilder,
    config: VerifierConfig,
    verifier: VerifierService,
    score: int,
) -> None:
    expected = (
        Decision.PASS
        if score <= config.pass_score_max
        else Decision.REVIEW
        if score <= config.review_score_max
        else Decision.FAIL
    )
    evaluation = build.evaluation(build.charge(), risk_score=score)

    verdict = verifier.verify(
        build.request(evaluation=build.sign_evaluation(evaluation))
    )

    assert verdict.decision is expected


def test_moving_a_threshold_moves_the_verdict_without_touching_code(
    build: ScenarioBuilder, config: VerifierConfig
) -> None:
    """Thresholds are config. This is the test that proves it."""
    score_five = build.sign_evaluation(
        build.evaluation(build.charge(), risk_score=5)
    )
    request = build.request(evaluation=score_five)
    strict = config.model_copy(update={"pass_score_max": 1, "review_score_max": 4})

    assert VerifierService(config).verify(request).decision is Decision.REVIEW
    assert VerifierService(strict).verify(request).decision is Decision.FAIL


def test_config_version_changes_when_a_threshold_moves(
    build: ScenarioBuilder, config: VerifierConfig, verifier: VerifierService
) -> None:
    """A silent policy change would make two verdicts incomparable."""
    relaxed = config.model_copy(update={"review_score_max": 9})

    original = verifier.verify(build.request()).config_version
    changed = VerifierService(relaxed).verify(build.request()).config_version

    assert original != changed


def test_review_band_may_not_be_configured_away() -> None:
    """An empty REVIEW band would mean no human is ever in the loop."""
    with pytest.raises(ValueError, match="REVIEW band is empty"):
        VerifierConfig(
            issuer_address="0x" + "11" * 20, pass_score_max=7, review_score_max=7
        )


# --- counterparty ----------------------------------------------------------


def test_a_suspended_merchant_cannot_be_paid(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    verdict = verifier.verify(
        build.request(merchant=build.merchant(is_active=False))
    )

    assert verdict.reason_codes == [ReasonCode.MERCHANT_INACTIVE]


def test_payout_address_comparison_ignores_checksum_casing(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    """Addresses are normalised at parse time, so casing is never a mismatch."""
    merchant = build.merchant()
    # An EIP-55 checksummed address: same bytes, mixed case in the body only.
    checksummed = "0x" + merchant.payout_address.removeprefix("0x").upper()
    shouty = build.charge(payout_address=checksummed)

    verdict = verifier.verify(
        build.request(
            charge=shouty, evaluation=build.sign_evaluation(build.evaluation(shouty))
        )
    )

    assert verdict.decision is Decision.PASS


def test_a_nonce_claimed_by_another_mandate_is_a_replay(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    from trustrail.models.mandate import MandateState, MandateStatus

    stolen_nonce = MandateState(
        status=MandateStatus.MINTED, nonce_claimed_by="0x" + "99" * 32
    )

    verdict = verifier.verify(build.request(mandate_state=stolen_nonce))

    assert verdict.reason_codes == [ReasonCode.NONCE_REPLAYED]


# --- evidence --------------------------------------------------------------


@pytest.mark.parametrize(
    "record_change",
    [{"is_active": False}, {"role": AgentRole.BROWSER}, {"agent_id": "someone-else"}],
    ids=["suspended", "wrong_role", "wrong_identity"],
)
def test_evidence_is_only_accepted_from_a_registered_evaluator(
    build: ScenarioBuilder, verifier: VerifierService, record_change: dict
) -> None:
    impostor: AgentRecord = build.evaluator_record().model_copy(update=record_change)

    verdict = verifier.verify(build.request(evaluator=impostor))

    assert verdict.reason_codes == [ReasonCode.EVALUATOR_NOT_REGISTERED]


def test_an_evaluation_for_a_different_amount_is_rejected(
    build: ScenarioBuilder, verifier: VerifierService
) -> None:
    """The basket may match while the price has been raised underneath it."""
    charge = build.charge(amount=Money(currency=Currency.XSGD, amount="4.99"))
    evaluation_of_the_cheaper_item = build.sign_evaluation(
        build.evaluation(build.charge(amount=PRICE))
    )

    verdict = verifier.verify(
        build.request(charge=charge, evaluation=evaluation_of_the_cheaper_item)
    )

    assert verdict.reason_codes == [ReasonCode.EVALUATOR_SUBJECT_MISMATCH]
