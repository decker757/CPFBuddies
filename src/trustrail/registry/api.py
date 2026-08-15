"""HTTP surface for the Merchant and Agent registries.

`GET /merchants` is the discovery endpoint CLAUDE.md names: it answers "who can
an agent legitimately buy from". The writes go through the Onboarding
Orchestrator rather than straight at the directories, so registering a merchant
is one code path whether it came from a seed script or a request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from trustrail.models.primitives import HexAddress, MerchantText, ShortText
from trustrail.models.registry import AgentRecord, AgentRole, MerchantRecord
from trustrail.orchestrator.onboarding import OnboardingOrchestrator
from trustrail.ports import AgentDirectory, MerchantDirectory


class RegisterMerchantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_id: ShortText
    name: MerchantText
    payout_address: Annotated[
        HexAddress,
        Field(
            description="The only address this platform can ever be paid at. "
            "Agreed with the platform at onboarding, never read from a listing."
        ),
    ]


class RegisterAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: ShortText
    role: AgentRole
    address: HexAddress


def build_router(
    *,
    onboarding: OnboardingOrchestrator,
    merchants: MerchantDirectory,
    agents: AgentDirectory,
) -> APIRouter:
    router = APIRouter(tags=["registry"])

    @router.get("/merchants")
    def list_merchants() -> list[MerchantRecord]:
        """Every registered platform. This is the discovery surface."""
        return merchants.list_all()

    @router.get("/merchants/{merchant_id}")
    def get_merchant(merchant_id: str) -> MerchantRecord:
        record = merchants.get(merchant_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such merchant")
        return record

    @router.post("/merchants", status_code=status.HTTP_201_CREATED)
    def register_merchant(request: RegisterMerchantRequest) -> MerchantRecord:
        return onboarding.register_merchant(
            merchant_id=request.merchant_id,
            name=request.name,
            payout_address=request.payout_address,
        )

    @router.post("/merchants/{merchant_id}/suspend")
    def suspend_merchant(merchant_id: str) -> MerchantRecord:
        record = onboarding.suspend_merchant(merchant_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such merchant")
        return record

    @router.get("/agents")
    def list_agents() -> list[AgentRecord]:
        return agents.list_all()

    @router.post("/agents", status_code=status.HTTP_201_CREATED)
    def register_agent(request: RegisterAgentRequest) -> AgentRecord:
        return onboarding.register_agent(
            agent_id=request.agent_id, role=request.role, address=request.address
        )

    return router
