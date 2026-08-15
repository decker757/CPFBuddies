"""The queue boundary between verification and settlement.

CLAUDE.md puts SQS plus a DLQ here so a settlement failure cannot take the verification path
down with it. The protocol is deliberately SQS-shaped -- receive, ack, nack, dead-letter -- so
the in-memory implementation teaches the same failure semantics the real one has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from trustrail.settlement.models import SettlementRequest


@dataclass(frozen=True)
class QueueMessage:
    """One settlement request in flight."""

    message_id: str
    request: SettlementRequest
    receive_count: int = 1
    receipt_handle: str | None = None


@runtime_checkable
class SettlementQueue(Protocol):
    """A work queue with at-least-once delivery and a dead-letter destination."""

    def publish(self, request: SettlementRequest) -> str:
        """Enqueue a PASS verdict and its charge. Returns the message id."""
        ...

    def receive(self, limit: int = 1) -> list[QueueMessage]:
        """Take up to ``limit`` messages, leaving them invisible until acked or nacked."""
        ...

    def ack(self, message: QueueMessage) -> None:
        """Delete a message that was handled."""
        ...

    def nack(self, message: QueueMessage) -> None:
        """Return a message for retry, dead-lettering it once attempts are exhausted."""
        ...
