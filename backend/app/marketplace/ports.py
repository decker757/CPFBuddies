from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.contracts import ListingsResponse, PurchaseReceipt


class Clock(Protocol):
    def now(self) -> datetime: ...


class QuoteIdGenerator(Protocol):
    def new_id(self) -> str: ...


class QuoteRepository(Protocol):
    def save(self, quote: ListingsResponse) -> None: ...

    def get(self, quote_id: str) -> ListingsResponse | None: ...

    def delete_expired(self, before: datetime) -> int: ...

    def save_receipt_if_absent(self, receipt: PurchaseReceipt) -> PurchaseReceipt | None:
        """Save once, returning the existing receipt when already consumed."""
        ...
