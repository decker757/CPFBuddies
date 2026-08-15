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
from trustrail.models.verdict import ReasonCode, Verdict

SummaryText = Annotated[str, StringConstraints(min_length=1, max_length=500)]

#: A rail's identifier for what it did: a 66-character transaction hash, a card id. Wider than
#: ShortText because a 0x-prefixed 32-byte hash is 66 characters and would not fit.
ReferenceText = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class AuditEventType(StrEnum):
    MANDATE_MINTED = "MANDATE_MINTED"
    MANDATE_BOUND = "MANDATE_BOUND"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_CONSUMED = "MANDATE_CONSUMED"
    KILL_SWITCH_SET = "KILL_SWITCH_SET"
    VERDICT_ISSUED = "VERDICT_ISSUED"
    # Settlement, written by workstream C once a PASS has been acted on.
    SETTLEMENT_SETTLED = "SETTLEMENT_SETTLED"
    SETTLEMENT_REFUSED = "SETTLEMENT_REFUSED"
    SETTLEMENT_FAILED = "SETTLEMENT_FAILED"


class SettlementOutcome(StrEnum):
    """What a rail did with an instruction.

    REFUSED and ERROR are kept apart deliberately: REFUSED means the rail worked and declined
    (an onchain revert, a card authorisation denied) and retrying cannot change the answer,
    while ERROR means the rail itself failed and redelivery may succeed. Conflating them
    retries reverted transactions forever, burning gas for nothing.
    """

    SETTLED = "SETTLED"
    REFUSED = "REFUSED"
    ERROR = "ERROR"


class SettlementRecord(BaseModel):
    """How a charge was executed, once the Verifier had already allowed it.

    Carried on the audit entry rather than in free text because the dashboard renders
    ``explorer_url`` as a link -- CLAUDE.md's demo ends on a transaction opened in Snowtrace,
    and a hash buried in prose cannot be linked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rail: ShortText
    outcome: SettlementOutcome
    reference: ReferenceText | None = Field(
        default=None,
        description="Transaction hash on the onchain rail, card id on the card rail.",
    )
    explorer_url: SummaryText | None = None
    reason_code: ReasonCode | None = Field(
        default=None,
        description="Present when the rail refused for a reason the wire contract names.",
    )
    detail: SummaryText | None = None


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
    settlement: SettlementRecord | None = Field(
        default=None,
        description="Present on the SETTLEMENT_* events, carrying the rail and its result.",
    )
