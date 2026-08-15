"""HTTP surface for the Mandate Service.

Routers are built by a factory that closes over the service, rather than
reaching for module-level globals. Tests construct a service with in-memory
stores and get a fully working app with no patching.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from trustrail.errors import MandateNotFound
from trustrail.mandate.service import MandateService
from trustrail.models.audit import AuditEntry
from trustrail.models.mandate import (
    IntentText,
    MandateBinding,
    MandateRecord,
)
from trustrail.models.money import Money
from trustrail.models.primitives import HexAddress, ShortText

#: Mandate lifetimes: at least a minute to be usable, at most a day so a
#: forgotten mandate cannot sit spendable indefinitely.
TtlSeconds = Annotated[int, Field(ge=60, le=86_400)]


class MintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: HexAddress
    agent_id: ShortText
    max_amount: Money
    intent: IntentText
    ttl_seconds: TtlSeconds = 600


class BindRequest(BaseModel):
    """A human approving a REVIEW: name the merchant and the basket."""

    model_config = ConfigDict(extra="forbid")

    binding: MandateBinding
    approved_by: ShortText


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ShortText
    reason: Annotated[str, Field(min_length=1, max_length=200)]


class KillSwitchRequest(BaseModel):
    """Halt settlement globally, or for one buyer."""

    model_config = ConfigDict(extra="forbid")

    active: bool
    actor: ShortText
    principal: HexAddress | None = Field(
        default=None, description="Omit to operate the global switch."
    )


def build_router(service: MandateService) -> APIRouter:
    router = APIRouter(tags=["mandates"])

    @router.post("/mandates", status_code=status.HTTP_201_CREATED)
    def mint(request: MintRequest) -> MandateRecord:
        return service.mint(
            principal=request.principal,
            agent_id=request.agent_id,
            max_amount=request.max_amount,
            intent=request.intent,
            ttl=timedelta(seconds=request.ttl_seconds),
        )

    @router.get("/mandates/{mandate_id}")
    def get(mandate_id: str) -> MandateRecord:
        record = service.get(mandate_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mandate")
        return record

    @router.get("/mandates")
    def list_for_principal(principal: str) -> list[MandateRecord]:
        return service.list_for_principal(principal)

    @router.post("/mandates/{mandate_id}/bind")
    def bind(mandate_id: str, request: BindRequest) -> MandateRecord:
        return service.bind(
            mandate_id, request.binding, approved_by=request.approved_by
        )

    @router.post("/mandates/{mandate_id}/revoke")
    def revoke(mandate_id: str, request: RevokeRequest) -> MandateRecord:
        return service.revoke(
            mandate_id, actor=request.actor, reason=request.reason
        )

    @router.post("/mandates/{mandate_id}/consume")
    def consume(mandate_id: str, request: RevokeRequest) -> MandateRecord:
        """Spend a mandate. Succeeds exactly once, whoever calls it."""
        return service.consume(mandate_id, actor=request.actor)

    @router.get("/mandates/{mandate_id}/audit")
    def history(mandate_id: str) -> list[AuditEntry]:
        if service.get(mandate_id) is None:
            raise MandateNotFound(mandate_id)
        return service.history(mandate_id)

    @router.post("/kill-switch", status_code=status.HTTP_204_NO_CONTENT)
    def set_kill_switch(request: KillSwitchRequest) -> None:
        if request.principal is None:
            service.halt_all(active=request.active, actor=request.actor)
        else:
            service.halt_principal(
                request.principal, active=request.active, actor=request.actor
            )

    return router
