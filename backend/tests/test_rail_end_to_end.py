"""The whole rail, from a buyer's words to a settlement decision.

Every other test in this repo exercises one workstream against fixtures. This
one runs the real stub marketplace, the real Browser Agent, the real Evaluator
and the real Verifier in one process, and asserts the four outcomes CLAUDE.md's
demo depends on. If any workstream changes its mind about a shape, this fails
here rather than during the demo.

The catalogue SKUs are the demo script:

  TB-SOFT-2PK      the clean purchase          -> PASS
  TB-INJECTION     prompt injection in a listing -> FAIL
  GIFT-SUBSTITUTE  a gift card sold as a toothbrush -> FAIL
  TB-SUSPICIOUS    right-ish product, new seller, S$0.50 -> REVIEW
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from trustrail.models.mandate import MandateStatus
from trustrail.models.registry import AgentRole
from trustrail.models.review import ReviewOutcome
from trustrail.models.verdict import CheckKind, Decision, ReasonCode

from app.contracts import xsgd
from app.rail import BROWSER_AGENT_ID, build_rail

PRINCIPAL = "0x" + "ab" * 20
INTENT = "toothbrush under $5"
CAP = xsgd("5.00")


def buy(rail, **overrides):
    fields = {
        "principal": PRINCIPAL,
        "agent_id": BROWSER_AGENT_ID,
        "intent": INTENT,
        "max_amount": CAP,
        "ttl": timedelta(minutes=10),
    }
    return asyncio.run(rail.orchestrator.purchase(**(fields | overrides)))


@pytest.fixture
def clean():
    return build_rail(preferred_sku="TB-SOFT-2PK")


@pytest.fixture
def poisoned():
    return build_rail(preferred_sku="TB-INJECTION")


@pytest.fixture
def substituted():
    return build_rail(preferred_sku="GIFT-SUBSTITUTE")


@pytest.fixture
def suspicious():
    return build_rail(preferred_sku="TB-SUSPICIOUS")


class TestCleanPurchase:
    """Demo steps 1 to 6: intent, mandate, listing, score, PASS, settle."""

    def test_it_passes_and_reaches_the_settlement_queue(self, clean):
        outcome = buy(clean)

        assert outcome.decision is Decision.PASS
        assert outcome.verdict.reason_codes == []
        assert outcome.settling

    def test_it_charges_the_price_the_merchant_quoted(self, clean):
        outcome = buy(clean)

        assert outcome.charge.amount == xsgd("4.20")
        assert outcome.charge.sku == "TB-SOFT-2PK"

    def test_the_charge_is_bound_to_the_quote_the_agent_actually_saw(self, clean):
        """`basket_hash` and `quote_id` come from the merchant's own response."""
        outcome = buy(clean)

        assert outcome.charge.quote_id.startswith("q_")
        assert outcome.charge.basket_hash.startswith("0x")

    def test_what_reaches_the_queue_is_what_was_verified(self, clean):
        outcome = buy(clean)

        [message] = clean.queue.receive(limit=5)

        assert message.request.charge == outcome.charge
        assert message.request.verdict.decision is Decision.PASS
        assert message.request.human_approval is None


class TestPromptInjection:
    """Demo step 7. This is the one judges remember."""

    def test_an_injected_listing_fails(self, poisoned):
        outcome = buy(poisoned)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.INJECTION_SUSPECTED in outcome.verdict.reason_codes

    def test_it_never_reaches_a_human_and_never_reaches_the_queue(self, poisoned):
        outcome = buy(poisoned)

        assert outcome.hold is None
        assert not outcome.settling
        assert poisoned.queue.receive(limit=5) == []

    def test_the_evaluator_saw_the_injection_itself(self, poisoned):
        """The finding is the Evaluator's, signed by it, not the rail guessing."""
        outcome = buy(poisoned)

        [message] = [
            entry
            for entry in poisoned.mandates.history(outcome.mandate.mandate_id)
            if entry.event_type == "VERDICT_ISSUED"
        ]
        assert message.verdict.risk_score >= 8


class TestSubstitution:
    """A gift card sold as a toothbrush: the gap left by minting before choosing."""

    def test_a_substituted_product_fails(self, substituted):
        outcome = buy(substituted)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.INTENT_MISMATCH_SUSPECTED in outcome.verdict.reason_codes
        assert not outcome.settling


class TestReview:
    """Demo step 7b: where the human-in-the-loop story lands."""

    def test_an_unknown_seller_pricing_far_below_market_holds(self, suspicious):
        outcome = buy(suspicious)

        assert outcome.decision is Decision.REVIEW
        assert ReasonCode.SUSPICIOUS_SELLER_PRICING in outcome.verdict.reason_codes
        assert outcome.hold is not None
        assert not outcome.settling

    def test_the_approval_surface_gets_the_listing_the_price_and_the_reasons(
        self, suspicious
    ):
        buy(suspicious)
        [hold] = suspicious.orchestrator.pending_reviews()

        assert hold.charge.title == "Premium electric toothbrush"
        assert hold.charge.amount == xsgd("0.50")
        assert hold.evaluation.evaluation.reasons
        assert hold.approvable

    def test_approving_binds_the_mandate_and_settles(self, suspicious):
        outcome = buy(suspicious)

        approved = suspicious.orchestrator.approve_review(
            outcome.charge.charge_id, approved_by="ernest"
        )

        assert approved.hold.outcome is ReviewOutcome.APPROVED
        assert approved.mandate.status is MandateStatus.BOUND
        assert approved.settling

    def test_killing_revokes_the_mandate_and_settles_nothing(self, suspicious):
        outcome = buy(suspicious)

        killed = suspicious.orchestrator.kill_review(
            outcome.charge.charge_id, killed_by="ernest"
        )

        assert killed.hold.outcome is ReviewOutcome.KILLED
        assert killed.mandate.status is MandateStatus.REVOKED
        assert suspicious.queue.receive(limit=5) == []


class TestDeterministicChecksAreNotOverridable:
    def test_a_failing_charge_is_marked_as_a_fact_not_a_threshold(self, clean):
        """`failed_deterministically` is what the UI keys the override button off."""
        clean.mandates.halt_all(active=True, actor="ernest")

        outcome = buy(clean)

        assert outcome.verdict.failed_deterministically
        assert outcome.hold is None

    def test_a_judgement_fail_is_not_marked_deterministic(self, poisoned):
        """An injection FAIL is a threshold in config, and the payload says so."""
        outcome = buy(poisoned)

        assert not outcome.verdict.failed_deterministically
        judgement = [
            check
            for check in outcome.verdict.checks
            if check.decision is Decision.FAIL and check.kind is CheckKind.JUDGEMENT
        ]
        assert judgement


class TestRegistryIsLoadBearing:
    def test_an_unregistered_evaluator_id_fails_the_charge(self, clean):
        """Both of B's evaluator ids must be registered, not just the one seen in testing."""
        # Re-register the id B actually signs under, but with the wrong role.
        # Registering an evaluator as something else is the same failure as not
        # registering it: the Verifier will not read a score from an agent that
        # is not an active evaluator.
        clean.onboarding.register_agent(
            agent_id="evaluator-rules-v1",
            role=AgentRole.BROWSER,
            address=clean.evaluator_signer.address,
        )

        outcome = buy(clean)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.EVALUATOR_NOT_REGISTERED in outcome.verdict.reason_codes

    def test_suspending_the_merchant_stops_purchases(self, clean):
        clean.onboarding.suspend_merchant("mrc_stub_sg")

        outcome = buy(clean)

        assert outcome.decision is Decision.FAIL
        assert ReasonCode.MERCHANT_INACTIVE in outcome.verdict.reason_codes


class TestHttpSurface:
    def test_a_purchase_runs_over_http(self, clean):
        client = TestClient(clean.app)

        response = client.post(
            "/purchases",
            json={
                "principal": PRINCIPAL,
                "agent_id": BROWSER_AGENT_ID,
                "intent": INTENT,
                "max_amount": {"currency": "XSGD", "amount": "5.00"},
                "ttl_seconds": 600,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["verdict"]["decision"] == "PASS"
        assert body["queued_message_id"] is not None

    def test_a_rejected_purchase_is_still_a_200(self, poisoned):
        """A FAIL is a decision, not a failed request. The dashboard renders it."""
        client = TestClient(poisoned.app)

        response = client.post(
            "/purchases",
            json={
                "principal": PRINCIPAL,
                "agent_id": BROWSER_AGENT_ID,
                "intent": INTENT,
                "max_amount": {"currency": "XSGD", "amount": "5.00"},
            },
        )

        assert response.status_code == 200
        assert response.json()["verdict"]["decision"] == "FAIL"

    def test_merchants_are_discoverable(self, clean):
        client = TestClient(clean.app)

        response = client.get("/merchants")

        assert response.status_code == 200
        assert [m["merchant_id"] for m in response.json()] == ["mrc_stub_sg"]

    def test_a_review_can_be_listed_and_approved_over_http(self, suspicious):
        client = TestClient(suspicious.app)
        client.post(
            "/purchases",
            json={
                "principal": PRINCIPAL,
                "agent_id": BROWSER_AGENT_ID,
                "intent": INTENT,
                "max_amount": {"currency": "XSGD", "amount": "5.00"},
            },
        )

        [hold] = client.get("/reviews").json()
        approved = client.post(
            f"/reviews/{hold['charge_id']}/approve", json={"actor": "ernest"}
        )

        assert approved.status_code == 200
        assert approved.json()["hold"]["outcome"] == "APPROVED"
        assert client.get("/reviews").json() == []

    def test_answering_a_review_twice_is_a_conflict(self, suspicious):
        client = TestClient(suspicious.app)
        outcome = buy(suspicious)
        charge_id = outcome.charge.charge_id
        client.post(f"/reviews/{charge_id}/kill", json={"actor": "ernest"})

        again = client.post(f"/reviews/{charge_id}/kill", json={"actor": "ernest"})

        assert again.status_code == 409

    def test_an_unknown_review_is_a_404(self, clean):
        client = TestClient(clean.app)

        response = client.post(
            f"/reviews/0x{'99' * 32}/approve", json={"actor": "ernest"}
        )

        assert response.status_code == 404
