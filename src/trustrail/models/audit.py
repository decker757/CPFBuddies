"""The audit trail.

Append-only, keyed by mandate. Every state change and every verdict lands here,
which is what makes the claim auditable rather than merely asserted: the record
of *why* a charge was rejected outlives the request that rejected it.

This is also what the decision dashboard streams.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from trustrail.models.primitives import Hex32, ShortText, Timestamp
from trustrail.models.verdict import Verdict

SummaryText = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class AuditEventType(StrEnum):
    MANDATE_MINTED = "MANDATE_MINTED"
    MANDATE_BOUND = "MANDATE_BOUND"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_CONSUMED = "MANDATE_CONSUMED"
    KILL_SWITCH_SET = "KILL_SWITCH_SET"
    VERDICT_ISSUED = "VERDICT_ISSUED"


class AuditEntry(BaseModel):
    """One immutable record. Ordered by `occurred_at` within a mandate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Hex32
    mandate_id: Hex32
    event_type: AuditEventType
    occurred_at: Timestamp
    actor: ShortText = Field(
        description="Who or what caused this: a service name, an agent id, or "
        "the principal for a human approval."
    )
    summary: SummaryText
    verdict: Verdict | None = Field(
        default=None,
        description="Present on VERDICT_ISSUED, carrying the full check trace.",
    )
