from decimal import Decimal
from typing import Protocol

from trustrail.models.money import Money

from app.contracts import Listing, ModelAssessment


class EvaluationModelError(RuntimeError):
    """Raised when an external evaluation model cannot produce a valid assessment."""


class EvaluationModel(Protocol):
    model_id: str

    def assess(self, *, listing: Listing, intent: str, max_amount: Money) -> ModelAssessment: ...
