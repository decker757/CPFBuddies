"""Everything the Verifier needs, handed to it in one payload.

The Verifier makes no network calls and reads no clock. That is not fussiness:
it is what makes the one piece that must be defensible under questioning also
the one piece that is trivially testable. Every lookup — mandate state, merchant
record, evaluator record, kill switch, current time — happens in the caller and
arrives here as data.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trustrail.models.charge import Charge
from trustrail.models.evaluation import SignedEvaluatorOutput
from trustrail.models.mandate import MandateState, SignedMandate
from trustrail.models.primitives import Timestamp
from trustrail.models.registry import AgentRecord, MerchantRecord


class VerificationRequest(BaseModel):
    """A charge, its mandate, the evidence, and the state needed to judge them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signed_mandate: SignedMandate
    charge: Charge
    evaluation: SignedEvaluatorOutput
    mandate_state: MandateState
    merchant: MerchantRecord | None = Field(
        default=None,
        description="None means the Merchant Registry had no record — an "
        "unregistered counterparty, which is a deterministic FAIL.",
    )
    evaluator: AgentRecord | None = Field(
        default=None,
        description="None means the Agent Registry had no record for the "
        "evaluator that signed the evidence.",
    )
    kill_switch_active: bool = False
    now: Timestamp
