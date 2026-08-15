"""The Purchase Orchestrator, against a scripted agent.

Workstream B's real agents are exercised in `backend/tests/test_rail_end_to_end.py`.
These tests care about the part B cannot influence: given evidence saying X,
what does the orchestrator do about it. A fake shopper is the point rather than
a shortcut — it can produce evidence a real Evaluator never would, which is
exactly the input the rail has to survive.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from trustrail.clock import FrozenClock
from trustrail.contracts.keys import (
    EVALUATOR_ADDRESS,
    EVALUATOR_ID,
    ISSUER_PRIVATE_KEY,
    MERCHANT_ID,
    label_to_address,
)
from trustrail.contracts.scenarios import (
    DEMO_NOW,
    MERCHANT_PAYOUT,
    ScenarioBuilder,
    demo_config,
)
from trustrail.mandate.service import MandateService
from trustrail.models.candidate import PurchaseCandidate
from trustrail.models.evaluation import (
    EvaluationSubject,
    EvaluatorFlags,
    EvaluatorOutput,
)
from trustrail.models.mandate import MandateStatus
from trustrail.models.money import Currency, Money
from trustrail.models.registry import AgentRole
from trustrail.models.review import ReviewOutcome
from trustrail.models.verdict import Decision, ReasonCode
from trustrail.orchestrator.onboarding import OnboardingOrchestrator
from trustrail.orchestrator.ports import ShoppingResult
from trustrail.orchestrator.purchase import (
    PurchaseOrchestrator,
    ReviewNotFound,
    ReviewNotPending,
)
from trustrail.settlement.queue.memory import InMemorySettlementQueue
from trustrail.signing.local import LocalSigner
from trustrail.stores.memory import (
    InMemoryAgentDirectory,
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
    InMemoryMerchantDirectory,
    InMemoryReviewHoldStore,
)
from trustrail.verifier.service import VerifierService

PRINCIPAL = label_to_address("principal:demo")
CAP = Money(currency=Currency.XSGD, amount="5.00")
PRICE = Money(currency=Currency.XSGD, amount="4.20")
INTENT = "toothbrush under $5"


class ScriptedShopper:
    """An agent that returns exactly what a test tells it to.

    It signs with the Evaluator's registered key by default, so a test only has
    to state the thing it is actually varying — a risk score, a flag, a payout
    address, or a forged signature.
    """

    def __init__(
        self,
        build: ScenarioBuilder,
        *,
        candidate: PurchaseCandidate | None = None,
        risk_score: int = 2,
        flags: EvaluatorFlags | None = None,
        evaluator_id: str = EVALUATOR_ID,
        key: bytes | None = None,
    ) -> None:
        self._build = build
        self._candidate = candidate or _candidate()
        self._risk_score = risk_score
        self._flags = flags or EvaluatorFlags(
            intent_match=True,
            injection_suspected=False,
            price_far_below_market=False,
            seller_is_new=False,
        )
        self._evaluator_id = evaluator_id
        self._key = key

    async def shop(
        self, *, intent: str, max_amount: Money, mandate_id: str
    ) -> ShoppingResult:
        del intent, max_amount
        evaluation = EvaluatorOutput(
            evaluator_id=self._evaluator_id,
            subject=EvaluationSubject(
                mandate_id=mandate_id,
                basket_hash=self._candidate.basket_hash,
                amount=self._candidate.amount,
            ),
            risk_score=self._risk_score,
            flags=self._flags,
            reasons=["Scripted for a test."],
        )
        signed = (
            self._build.sign_evaluation(evaluation, key=self._key)
            if self._key is not None
            else self._build.sign_evaluation(evaluation)
        )
        return ShoppingResult(candidate=self._candidate, evaluation=signed)


def _candidate(**overrides) -> PurchaseCandidate:
    fields = {
        "merchant_id": MERCHANT_ID,
        "payout_address": MERCHANT_PAYOUT,
        "amount": PRICE,
        "basket_hash": "0x" + "be" * 32,
        "quote_id": "q_01HXTEST",
        "sku": "TB-SOFT-2PK",
        "title": "Soft bristle toothbrush, 2 pack",
        "quantity": 1,
    }
    return PurchaseCandidate(**(fields | overrides))


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(DEMO_NOW)


@pytest.fixture
def rail(build: ScenarioBuilder, clock: FrozenClock):
    """A whole orchestrator over in-memory parts, with the demo keys.

    The Mandate Service signs with the issuer key `demo_config` trusts. Wiring
    those two independently is how production works and how it breaks, so the
    tests wire them the same way.
    """
    config = demo_config()
    audit = InMemoryAuditLog()
    merchants = InMemoryMerchantDirectory()
    agents = InMemoryAgentDirectory()
    holds = InMemoryReviewHoldStore()
    queue = InMemorySettlementQueue()

    mandates = MandateService(
        signer=LocalSigner(ISSUER_PRIVATE_KEY),
        store=InMemoryMandateStore(),
        kill_switch=InMemoryKillSwitchStore(),
        audit=audit,
        domain=config.domain,
        clock=clock,
    )
    onboarding = OnboardingOrchestrator(merchants=merchants, agents=agents)
    onboarding.register_merchant(
        merchant_id=MERCHANT_ID, name="SG Mart", payout_address=MERCHANT_PAYOUT
    )
    onboarding.register_agent(
        agent_id=EVALUATOR_ID, role=AgentRole.EVALUATOR, address=EVALUATOR_ADDRESS
    )

    def assemble(shopper) -> PurchaseOrchestrator:
        return PurchaseOrchestrator(
            mandates=mandates,
            verifier=VerifierService(config),
            config=config,
            shopper=shopper,
            merchants=merchants,
            agents=agents,
            holds=holds,
            queue=queue,
            clock=clock,
        )

    return {
        "assemble": assemble,
        "mandates": mandates,
        "onboarding": onboarding,
        "merchants": merchants,
        "queue": queue,
        "audit": audit,
        "holds": holds,
    }


def run(rail, shopper, **overrides):
    """Run one purchase. Sync, like the rest of the suite -- `asyncio.run` here
    beats a plugin dependency for the handful of async entry points we have."""
    orchestrator = rail["assemble"](shopper)
    rail["orchestrator"] = orchestrator
    fields = {
        "principal": PRINCIPAL,
        "agent_id": "browser-1",
        "intent": INTENT,
        "max_amount": CAP,
        "ttl": timedelta(minutes=10),
    }
    return asyncio.run(orchestrator.purchase(**(fields | overrides)))


class TestCleanPurchase:
    def test_a_clean_candidate_passes_and_queues(self, rail, build):
        outcome = run(rail, ScriptedShopper(build))

        assert outcome.decision is Decision.PASS
        assert outcome.verdict.reason_codes == []
        assert outcome.settling
        assert len(rail["queue"].receive(limit=5)) == 1

    def test_the_mandate_is_minted_before_the_product_is_chosen(
        self, rail, build
    ):
        """The buyer approved a budget and an intent, not a SKU."""
        outcome = run(rail, ScriptedShopper(build))
        mandate = outcome.mandate.signed.mandate

        assert mandate.merchant_address is None
        assert mandate.basket_hash is None
        assert mandate.intent == INTENT

    def test_the_verdict_reaches_the_audit_trail(self, rail, build):
        outcome = run(rail, ScriptedShopper(build))

        events = [e.event_type for e in rail["mandates"].history(outcome.mandate.mandate_id)]
        assert "MANDATE_MINTED" in events
        assert "VERDICT_ISSUED" in events


class TestDeterministicFailures:
    """Facts. None of these may ever reach a human for approval."""

    def test_an_unregistered_merchant_fails_and_is_never_held(self, rail, build):
        shopper = ScriptedShopper(build, candidate=_candidate(merchant_id="mrc_unknown"))

        outcome = run(rail, shopper)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.MERCHANT_NOT_REGISTERED in outcome.verdict.reason_codes
        assert outcome.hold is None
        assert not outcome.settling

    def test_a_redirected_payout_fails(self, rail, build):
        """A scam seller inside a registered platform, supplying its own address."""
        shopper = ScriptedShopper(
            build, candidate=_candidate(payout_address=label_to_address("scammer"))
        )

        outcome = run(rail, shopper)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.PAYOUT_ADDRESS_MISMATCH in outcome.verdict.reason_codes

    def test_an_over_cap_charge_fails(self, rail, build):
        expensive = Money(currency=Currency.XSGD, amount="5.01")
        shopper = ScriptedShopper(build, candidate=_candidate(amount=expensive))

        outcome = run(rail, shopper)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.CHARGE_OVER_CAP in outcome.verdict.reason_codes

    def test_an_unregistered_evaluator_id_fails(self, rail, build):
        """The trap: B stamps a different id when its model is unreachable."""
        shopper = ScriptedShopper(build, evaluator_id="evaluator-nobody-registered")

        outcome = run(rail, shopper)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.EVALUATOR_NOT_REGISTERED in outcome.verdict.reason_codes

    def test_a_forged_evaluation_fails(self, rail, build):
        """A compromised Browser Agent writing itself a clean score."""
        from trustrail.contracts.keys import IMPOSTOR_PRIVATE_KEY

        shopper = ScriptedShopper(build, risk_score=1, key=IMPOSTOR_PRIVATE_KEY)

        outcome = run(rail, shopper)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.EVALUATOR_SIGNATURE_INVALID in outcome.verdict.reason_codes

    def test_the_kill_switch_stops_everything(self, rail, build):
        rail["mandates"].halt_all(active=True, actor="ernest")

        outcome = run(rail, ScriptedShopper(build))

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.KILL_SWITCH_ACTIVE in outcome.verdict.reason_codes

    def test_a_suspended_merchant_fails(self, rail, build):
        rail["onboarding"].suspend_merchant(MERCHANT_ID)

        outcome = run(rail, ScriptedShopper(build))

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.MERCHANT_INACTIVE in outcome.verdict.reason_codes


class TestReviewBand:
    def test_a_middling_score_holds_rather_than_settling(self, rail, build):
        outcome = run(rail, ScriptedShopper(build, risk_score=5))

        assert outcome.decision is Decision.REVIEW
        assert outcome.hold is not None
        assert outcome.hold.outcome is ReviewOutcome.PENDING
        assert not outcome.settling
        assert rail["queue"].receive(limit=5) == []

    def test_a_hold_never_outlives_its_mandate(self, rail, build):
        """The review window is 10 minutes and so is this mandate."""
        outcome = run(
            rail, ScriptedShopper(build, risk_score=5), ttl=timedelta(minutes=2)
        )

        assert outcome.hold.deadline == DEMO_NOW + timedelta(minutes=2)

    def test_the_hold_carries_what_the_human_needs_to_decide(self, rail, build):
        outcome = run(rail, ScriptedShopper(build, risk_score=5))
        hold = outcome.hold

        assert hold.charge.title == "Soft bristle toothbrush, 2 pack"
        assert hold.charge.amount == PRICE
        assert hold.evaluation.evaluation.reasons == ["Scripted for a test."]
        assert hold.approvable


class TestApproval:
    def test_approving_binds_re_verifies_and_settles(self, rail, build):
        outcome = run(rail, ScriptedShopper(build, risk_score=5))

        approved = rail["orchestrator"].approve_review(
            outcome.charge.charge_id, approved_by="ernest"
        )

        assert approved.hold.outcome is ReviewOutcome.APPROVED
        assert approved.mandate.status is MandateStatus.BOUND
        assert approved.mandate.signed.mandate.is_bound
        assert approved.settling

    def test_the_bound_mandate_commits_to_the_registered_address(
        self, rail, build
    ):
        """Never to the address the listing claimed, even when they agree."""
        outcome = run(rail, ScriptedShopper(build, risk_score=5))

        approved = rail["orchestrator"].approve_review(
            outcome.charge.charge_id, approved_by="ernest"
        )

        assert approved.mandate.signed.mandate.merchant_address == MERCHANT_PAYOUT

    def test_the_queued_verdict_still_says_review(self, rail, build):
        """The audit trail must not claim the Verifier passed something it held."""
        outcome = run(rail, ScriptedShopper(build, risk_score=5))
        rail["orchestrator"].approve_review(
            outcome.charge.charge_id, approved_by="ernest"
        )

        [message] = rail["queue"].receive(limit=5)

        assert message.request.verdict.decision is Decision.REVIEW
        assert ReasonCode.RISK_SCORE_REVIEW_BAND in message.request.verdict.reason_codes
        assert message.request.human_approval.approved_by == "ernest"

    def test_approval_is_recorded_against_the_mandate(self, rail, build):
        outcome = run(rail, ScriptedShopper(build, risk_score=5))
        rail["orchestrator"].approve_review(
            outcome.charge.charge_id, approved_by="ernest"
        )

        bound = [
            entry
            for entry in rail["mandates"].history(outcome.mandate.mandate_id)
            if entry.event_type == "MANDATE_BOUND"
        ]
        assert [entry.actor for entry in bound] == ["ernest"]

    def test_a_hold_cannot_be_approved_twice(self, rail, build):
        outcome = run(rail, ScriptedShopper(build, risk_score=5))
        charge_id = outcome.charge.charge_id
        rail["orchestrator"].approve_review(charge_id, approved_by="ernest")

        with pytest.raises(ReviewNotPending):
            rail["orchestrator"].approve_review(charge_id, approved_by="ernest")

    def test_a_lapsed_hold_cannot_be_approved(self, rail, build, clock):
        outcome = run(rail, ScriptedShopper(build, risk_score=5))
        clock.advance_to(outcome.hold.deadline + timedelta(seconds=1))

        with pytest.raises(ReviewNotPending):
            rail["orchestrator"].approve_review(
                outcome.charge.charge_id, approved_by="ernest"
            )

        assert rail["holds"].get(outcome.charge.charge_id).outcome is ReviewOutcome.EXPIRED

    def test_an_unknown_charge_is_not_found(self, rail, build):
        run(rail, ScriptedShopper(build, risk_score=5))

        with pytest.raises(ReviewNotFound):
            rail["orchestrator"].approve_review("0x" + "99" * 32, approved_by="ernest")


class TestKill:
    def test_killing_revokes_the_mandate(self, rail, build):
        outcome = run(rail, ScriptedShopper(build, risk_score=5))

        killed = rail["orchestrator"].kill_review(
            outcome.charge.charge_id, killed_by="ernest"
        )

        assert killed.hold.outcome is ReviewOutcome.KILLED
        assert killed.mandate.status is MandateStatus.REVOKED
        assert not killed.settling
        assert rail["queue"].receive(limit=5) == []

    def test_a_killed_mandate_cannot_be_reused(self, rail, build):
        """Rejecting one product must not leave authority behind for another."""
        outcome = run(rail, ScriptedShopper(build, risk_score=5))
        rail["orchestrator"].kill_review(outcome.charge.charge_id, killed_by="ernest")

        assert rail["holds"].list_pending(DEMO_NOW) == []
        assert (
            rail["mandates"].get(outcome.mandate.mandate_id).status
            is MandateStatus.REVOKED
        )
