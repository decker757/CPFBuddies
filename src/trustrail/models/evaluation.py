"""The Evaluator Agent's output.

The Evaluator is an LLM reading attacker-controlled listing text, so it can be
fooled. That is precisely why its output is structured evidence for the Verifier
rather than a decision: no free text from the Evaluator is ever passed
downstream as an instruction, and the deterministic checks run regardless of
what score it returns.

Two things make the output hard to forge:

- it is signed by a key registered to the Evaluator in the Agent Registry, so a
  compromised Browser Agent cannot mint itself a clean score;
- it names the `subject` it evaluated, so a clean score cannot be lifted off one
  charge and replayed onto another.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from trustrail.models.money import Money
from trustrail.models.primitives import Hex32, HexSignature, ShortText

#: Human-readable justification shown on the dashboard and the REVIEW screen.
#: Capped in both count and length: a 4000-word explanation is itself a signal.
ReasonText = Annotated[str, StringConstraints(min_length=1, max_length=280)]

MIN_RISK_SCORE = 1
MAX_RISK_SCORE = 10


class EvaluationSubject(BaseModel):
    """Exactly what was evaluated, so the verdict cannot be reused elsewhere."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mandate_id: Hex32
    basket_hash: Hex32
    amount: Money


class EvaluatorFlags(BaseModel):
    """Structured findings. Each one maps to a judgement check in the Verifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_match: bool = Field(
        description="Does the candidate product match what the buyer asked for, "
        "or is it a substitution?"
    )
    injection_suspected: bool = Field(
        description="Does the listing text contain instructions aimed at the agent?"
    )
    price_far_below_market: bool
    seller_is_new: bool


class EvaluatorOutput(BaseModel):
    """A risk score with its evidence. Structured output only, never free text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluator_id: ShortText
    subject: EvaluationSubject
    risk_score: Annotated[int, Field(ge=MIN_RISK_SCORE, le=MAX_RISK_SCORE)]
    flags: EvaluatorFlags
    reasons: Annotated[list[ReasonText], Field(max_length=10)]


class SignedEvaluatorOutput(BaseModel):
    """Evaluator output plus its signature.

    The digest here is a keccak256 over canonical JSON rather than EIP-712: this
    payload is evidence for the offchain Verifier and never reaches a contract,
    so there is nothing to recompute in Solidity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation: EvaluatorOutput
    digest: Hex32
    signature: HexSignature
