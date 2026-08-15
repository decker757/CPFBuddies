"""Registry records the Verifier reads but does not own.

The Merchant Registry and Agent Registry are separate atomic services. Workstream
A only defines the shape it consumes, so the Verifier can stay a pure function:
the caller looks the records up and passes them in.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from trustrail.models.primitives import HexAddress, MerchantText, ShortText


class MerchantRecord(BaseModel):
    """A registered merchant platform.

    The registry knows the *platform*, not the sellers on it. `payout_address`
    is the whole point: XSGD can only ever reach this address, so a scam seller
    inside an otherwise legitimate marketplace cannot redirect funds, and the
    platform stays accountable for its own sellers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    merchant_id: ShortText
    name: MerchantText
    payout_address: HexAddress
    is_active: bool = True


class AgentRole(StrEnum):
    BROWSER = "BROWSER"
    EVALUATOR = "EVALUATOR"


class AgentRecord(BaseModel):
    """An internal agent that signs its output for the audit trail.

    Registering the Evaluator's key is what stops a compromised Browser Agent
    forging itself a clean risk score.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: ShortText
    role: AgentRole
    address: HexAddress
    is_active: bool = True
