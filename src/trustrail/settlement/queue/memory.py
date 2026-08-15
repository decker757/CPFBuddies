"""In-memory queue.

Runs the whole settlement path with no AWS. Models the parts of SQS that change behaviour --
at-least-once delivery, in-flight invisibility, a redrive limit and a dead-letter queue -- so
code written against it does not acquire habits that break on the real thing.

Single-process only. Swap in :class:`trustrail.settlement.queue.sqs.SqsQueue` for anything else.
"""

from __future__ import annotations

import itertools
from collections import deque
from threading import RLock

from trustrail.settlement.models import SettlementRequest
from trustrail.settlement.queue.base import QueueMessage

DEFAULT_MAX_RECEIVES = 3


class InMemorySettlementQueue:
    """FIFO queue with a redrive policy."""

    def __init__(self, max_receives: int = DEFAULT_MAX_RECEIVES) -> None:
        self._pending: deque[QueueMessage] = deque()
        self._in_flight: dict[str, QueueMessage] = {}
        self._dead_letter: list[QueueMessage] = []
        self._ids = itertools.count(1)
        self._max_receives = max_receives
        self._lock = RLock()

    def publish(self, request: SettlementRequest) -> str:
        with self._lock:
            message = QueueMessage(
                message_id=f"msg_{next(self._ids)}", request=request, receive_count=0
            )
            self._pending.append(message)
            return message.message_id

    def receive(self, limit: int = 1) -> list[QueueMessage]:
        with self._lock:
            taken: list[QueueMessage] = []
            while self._pending and len(taken) < limit:
                message = self._pending.popleft()
                delivered = QueueMessage(
                    message_id=message.message_id,
                    request=message.request,
                    receive_count=message.receive_count + 1,
                    receipt_handle=message.message_id,
                )
                self._in_flight[delivered.message_id] = delivered
                taken.append(delivered)
            return taken

    def ack(self, message: QueueMessage) -> None:
        with self._lock:
            self._in_flight.pop(message.message_id, None)

    def nack(self, message: QueueMessage) -> None:
        with self._lock:
            self._in_flight.pop(message.message_id, None)
            if message.receive_count >= self._max_receives:
                self._dead_letter.append(message)
            else:
                self._pending.append(message)

    # ---- test and demo affordances, not part of the protocol -----------------------------

    @property
    def pending(self) -> list[QueueMessage]:
        with self._lock:
            return list(self._pending)

    @property
    def in_flight(self) -> list[QueueMessage]:
        with self._lock:
            return list(self._in_flight.values())

    @property
    def dead_letter(self) -> list[QueueMessage]:
        with self._lock:
            return list(self._dead_letter)
