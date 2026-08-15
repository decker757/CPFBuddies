"""The seam between workstream B and workstream A.

B's Evaluator produces findings. A's Verifier will not read the risk score until it has checked
that the evidence was signed by a registered evaluator and bound to this exact basket. These
tests drive B's real agent and A's real Verifier, so if either side changes its mind about the
shape, this fails here rather than during the demo.
"""

from __future__ import annotations

import pytest

from app.agents.evaluator import EvaluatorAgent
from app.contracts import Listing, xsgd
from app.evidence import to_signed_evidence
from app.marketplace.catalog import CATALOG
from trustrail.contracts.keys import (
    EVALUATOR_ADDRESS,
    EVALUATOR_PRIVATE_KEY,
    IMPOSTOR_PRIVATE_KEY,
)
from trustrail.models.registry import AgentRecord, AgentRole
from trustrail.contracts.scenarios import ScenarioBuilder, demo_config
from trustrail.models.verdict import Decision, ReasonCode
from trustrail.signing.local import LocalSigner
from trustrail.verifier.service import VerifierService


def listing(sku: str) -> Listing:
    return next(item for item in CATALOG if item.sku == sku)


@pytest.fixture
def build() -> ScenarioBuilder:
    return ScenarioBuilder()


@pytest.fixture
def verifier() -> VerifierService:
    return VerifierService(demo_config())


@pytest.fixture
def evaluator_signer() -> LocalSigner:
    """The key the Agent Registry has on file for the evaluator."""
    return LocalSigner(EVALUATOR_PRIVATE_KEY)


def evidence_for(build: ScenarioBuilder, sku: str, signer: LocalSigner):
    """Run B's evaluator over a real catalogue listing and sign the result as A expects.

    Returns the Agent Registry record alongside, carrying the id B's evaluator actually
    stamped on its output. That id is a deployment requirement, not a test convenience: the
    Onboarding Orchestrator has to register **both** of B's evaluator ids -- `evaluator-rules-v1`
    when the model is unreachable and `evaluator-hybrid-nova-v1` when it is not -- or a
    degraded evaluator produces evidence the Verifier rejects as unregistered.
    """
    charge = build.charge()
    output = EvaluatorAgent().evaluate(
        listing=listing(sku),
        intent="toothbrush under $5",
        max_amount=xsgd("5.00"),
    )
    signed = to_signed_evidence(
        output,
        mandate_id=charge.mandate_id,
        basket_hash=charge.basket_hash,
        amount=charge.amount,
        signer=signer,
    )
    registered = AgentRecord(
        agent_id=output.evaluator_id,
        role=AgentRole.EVALUATOR,
        address=EVALUATOR_ADDRESS,
    )
    return charge, signed, registered


class TestCleanListing:
    def test_verifier_passes_evidence_from_b(self, build, verifier, evaluator_signer):
        charge, evidence, registered = evidence_for(build, "TB-SOFT-2PK", evaluator_signer)

        verdict = verifier.verify(build.request(charge=charge, evaluation=evidence, evaluator=registered))

        assert verdict.decision is Decision.PASS, verdict.reason_codes
        assert verdict.risk_score == evidence.evaluation.risk_score

    def test_b_flags_map_onto_a_flags(self, build, evaluator_signer):
        _, evidence, _registered = evidence_for(build, "TB-SOFT-2PK", evaluator_signer)
        flags = evidence.evaluation.flags

        assert flags.intent_match is True
        assert flags.injection_suspected is False


class TestPoisonedListing:
    def test_injection_is_carried_through_and_fails(self, build, verifier, evaluator_signer):
        # B's evaluator scores the injected listing above the review band; A fails it without
        # asking a human. Same demo beat as CLAUDE.md step 7, proven across both workstreams.
        charge, evidence, registered = evidence_for(build, "TB-INJECTION", evaluator_signer)

        verdict = verifier.verify(build.request(charge=charge, evaluation=evidence, evaluator=registered))

        assert evidence.evaluation.flags.injection_suspected is True
        assert verdict.decision is Decision.FAIL
        assert ReasonCode.INJECTION_SUSPECTED in verdict.reason_codes


class TestForgery:
    def test_a_rejects_evidence_signed_by_the_wrong_key(self, build, verifier):
        # A compromised Browser Agent writing itself a clean score. The Agent Registry knows
        # which key the evaluator uses, so the forgery does not verify -- and this is only
        # checkable because B signs its output at all.
        charge, evidence, registered = evidence_for(
            build, "TB-SOFT-2PK", LocalSigner(IMPOSTOR_PRIVATE_KEY)
        )

        verdict = verifier.verify(build.request(charge=charge, evaluation=evidence, evaluator=registered))

        assert verdict.decision is Decision.FAIL
        assert ReasonCode.EVALUATOR_SIGNATURE_INVALID in verdict.reason_codes

    def test_a_rejects_evidence_bound_to_a_different_basket(
        self, build, verifier, evaluator_signer
    ):
        # A genuine, correctly signed evaluation -- for someone else's basket. Without the
        # subject binding, signing the output would buy nothing.
        charge, evidence, registered = evidence_for(build, "TB-SOFT-2PK", evaluator_signer)
        other_charge = build.charge(basket_hash="0x" + "cd" * 32)

        verdict = verifier.verify(
            build.request(charge=other_charge, evaluation=evidence, evaluator=registered)
        )

        assert verdict.decision is Decision.FAIL
        assert ReasonCode.EVALUATOR_SUBJECT_MISMATCH in verdict.reason_codes
