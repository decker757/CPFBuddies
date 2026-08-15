"""The settlement worker.

CLAUDE.md: "Settlement Worker. SQS consumer, not an orchestrator." It makes no decisions about
whether a payment is allowed. It takes verdicts the Verifier already reached, hands them to a
rail, and records what happened.

Three outcomes drive three different behaviours:

  SETTLED   money moved            -> record, ack
  REFUSED   the rail said no       -> record, ack. Retrying will not change the answer.
  ERROR     the rail itself broke  -> record, nack. Redelivery may succeed.

Confusing REFUSED with ERROR is the expensive mistake here: a reverted transaction retried
forever burns gas and never settles.
"""

from __future__ import annotations

import logging
from threading import Event
from typing import Mapping

from trustrail.models.audit import (
    AuditEntry,
    AuditEventType,
    SettlementOutcome,
    SettlementRecord,
)
from trustrail.models.primitives import new_hex32
from trustrail.ports import AuditLog, Clock
from trustrail.settlement.models import (
    SettlementInstruction,
    SettlementReceipt,
    SettlementRequest,
)
from trustrail.settlement.queue.base import QueueMessage, SettlementQueue
from trustrail.settlement.rails.base import SettlementRail

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 1.0
DEFAULT_ACTOR = "settlement-worker"

_EVENT_TYPES: dict[SettlementOutcome, AuditEventType] = {
    SettlementOutcome.SETTLED: AuditEventType.SETTLEMENT_SETTLED,
    SettlementOutcome.REFUSED: AuditEventType.SETTLEMENT_REFUSED,
    SettlementOutcome.ERROR: AuditEventType.SETTLEMENT_FAILED,
}


class SettlementWorker:
    """Consumes verified requests and settles them on the configured rail."""

    def __init__(
        self,
        queue: SettlementQueue,
        rails: Mapping[str, SettlementRail],
        audit: AuditLog,
        clock: Clock,
        rail_name: str,
        actor: str = DEFAULT_ACTOR,
    ) -> None:
        if rail_name not in rails:
            raise ValueError(f"rail '{rail_name}' is not configured; available: {sorted(rails)}")
        self._queue = queue
        self._rails = dict(rails)
        self._audit = audit
        self._clock = clock
        self._rail_name = rail_name
        self._actor = actor

    @property
    def rail(self) -> SettlementRail:
        """The rail in use. Selected by configuration; the worker never branches on which."""
        return self._rails[self._rail_name]

    def process_once(self, limit: int = 1) -> list[SettlementReceipt]:
        """Handle up to ``limit`` queued requests. Returns a receipt for each."""
        return [self._handle(message) for message in self._queue.receive(limit=limit)]

    def run_forever(
        self, stop: Event, poll_seconds: float = DEFAULT_POLL_SECONDS, batch: int = 1
    ) -> None:
        """Poll until ``stop`` is set.

        A rail fault must not kill the loop -- that is what the queue's redrive policy is for.
        """
        logger.info("settlement worker started on rail %s", self._rail_name)
        while not stop.is_set():
            try:
                if not self.process_once(limit=batch):
                    stop.wait(poll_seconds)
            except Exception:  # noqa: BLE001 - keep consuming; the message is already nacked
                logger.exception("settlement loop error")
                stop.wait(poll_seconds)
        logger.info("settlement worker stopped")

    def _handle(self, message: QueueMessage) -> SettlementReceipt:
        receipt = self._settle(message.request)
        self._audit.record(self._entry_for(receipt, message))

        if receipt.retryable:
            self._queue.nack(message)
        else:
            self._queue.ack(message)
        return receipt

    def _settle(self, request: SettlementRequest) -> SettlementReceipt:
        # Guard, not a decision. A non-PASS reaching this queue is an upstream bug, and no
        # amount of redelivery will fix it -- so it is recorded and acked rather than retried.
        if not request.settleable:
            logger.error(
                "refusing to settle a %s verdict for mandate %s (%s)",
                request.verdict.decision.value,
                request.verdict.mandate_id,
                [code.value for code in request.verdict.reason_codes],
            )
            return SettlementReceipt(
                mandate_id=request.verdict.mandate_id,
                rail=self._rail_name,
                status=SettlementOutcome.REFUSED,
                detail=f"not settleable: verdict was {request.verdict.decision.value}",
            )

        return self.rail.settle(SettlementInstruction.from_request(request))

    def _entry_for(self, receipt: SettlementReceipt, message: QueueMessage) -> AuditEntry:
        return AuditEntry(
            event_id=new_hex32(),
            mandate_id=receipt.mandate_id,
            event_type=_EVENT_TYPES[receipt.status],
            occurred_at=self._clock.now(),
            actor=self._actor,
            summary=(
                f"{receipt.status.value} on {receipt.rail} "
                f"(attempt {message.receive_count})"
            ),
            settlement=SettlementRecord(
                rail=receipt.rail,
                outcome=receipt.status,
                reference=receipt.reference,
                explorer_url=receipt.explorer_url,
                reason_code=receipt.reason_code,
                detail=receipt.detail,
            ),
        )
