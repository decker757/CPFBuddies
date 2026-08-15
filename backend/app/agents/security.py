from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class EvaluationSecurityEvent:
    event_type: str
    evaluator_id: str
    sku: str
    seller_id: str
    risk_score: int
    reason_codes: tuple[str, ...]


class SecurityEventSink(Protocol):
    def emit(self, event: EvaluationSecurityEvent) -> None: ...


class NullSecurityEventSink:
    def emit(self, event: EvaluationSecurityEvent) -> None:
        del event


class LoggingSecurityEventSink:
    """Emits metadata only; attacker-controlled title/description are excluded."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("cpf_buddies.security")

    def emit(self, event: EvaluationSecurityEvent) -> None:
        self._logger.warning("listing security signal", extra={"security_event": asdict(event)})
