"""Turning the Evaluator's findings into evidence the Verifier will accept.

Workstream A's Verifier does not take the Evaluator's word for anything. It checks two things
about the evidence before it reads the risk score at all:

- **the signature**, against the key registered for that evaluator in the Agent Registry, so a
  compromised Browser Agent cannot write itself a clean score;
- **the subject**, so a genuine low-risk evaluation cannot be lifted from one basket and
  replayed against another.

Both need the evaluator's own output bound to a mandate, a basket and an amount, and signed by
the evaluator itself. That is what this module produces. Signing anywhere further downstream
would defeat the point: the whole reason the signature exists is that everything between the
Evaluator and the Verifier is assumed compromisable.
"""

from __future__ import annotations

from app.contracts import EvaluatorOutput as AgentEvaluatorOutput
from app.contracts import RiskReasonCode
from trustrail.models.evaluation import (
    EvaluationSubject,
    EvaluatorFlags,
    EvaluatorOutput,
    SignedEvaluatorOutput,
)
from trustrail.models.money import Money
from trustrail.models.primitives import to_bytes
from trustrail.ports import Signer
from trustrail.signing.evidence import evaluation_digest

#: A.ReasonText caps each reason at 280 characters and the list at 10. B's rules can emit more
#: than that once the model adds its own, so the excess is dropped rather than rejected: losing
#: the eleventh reason is a worse outcome than failing to produce evidence at all.
MAX_REASONS = 10
MAX_REASON_LENGTH = 280


def to_evidence(
    output: AgentEvaluatorOutput,
    *,
    mandate_id: str,
    basket_hash: str,
    amount: Money,
) -> EvaluatorOutput:
    """Map the agent's internal findings onto the wire contract's shape."""
    codes = {reason.code for reason in output.reasons}
    return EvaluatorOutput(
        evaluator_id=output.evaluator_id,
        subject=EvaluationSubject(
            mandate_id=mandate_id, basket_hash=basket_hash, amount=amount
        ),
        risk_score=output.risk_score,
        flags=EvaluatorFlags(
            intent_match=output.intent_match,
            injection_suspected=output.injection_detected,
            price_far_below_market=RiskReasonCode.SUSPICIOUSLY_LOW_PRICE in codes,
            seller_is_new=RiskReasonCode.NEW_OR_UNRATED_SELLER in codes,
        ),
        reasons=[
            reason.detail[:MAX_REASON_LENGTH] for reason in output.reasons[:MAX_REASONS]
        ],
    )


def sign_evidence(evaluation: EvaluatorOutput, signer: Signer) -> SignedEvaluatorOutput:
    """Sign the evidence with the evaluator's own key.

    The digest is keccak256 over canonical JSON, computed by the same function the Verifier
    uses to check it -- so there is exactly one definition of what was signed.
    """
    digest = evaluation_digest(evaluation)
    return SignedEvaluatorOutput(
        evaluation=evaluation,
        digest=digest,
        signature=signer.sign(to_bytes(digest)),
    )


def to_signed_evidence(
    output: AgentEvaluatorOutput,
    *,
    mandate_id: str,
    basket_hash: str,
    amount: Money,
    signer: Signer,
) -> SignedEvaluatorOutput:
    """Map and sign in one step. This is what the Purchase Orchestrator calls."""
    return sign_evidence(
        to_evidence(output, mandate_id=mandate_id, basket_hash=basket_hash, amount=amount),
        signer,
    )
