from __future__ import annotations

import secrets
from datetime import UTC, datetime
from threading import Lock

from app.contracts import ListingsResponse, PurchaseReceipt


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SecureQuoteIdGenerator:
    def new_id(self) -> str:
        return f"q_{secrets.token_hex(12)}"


class InMemoryQuoteRepository:
    """Thread-safe demo adapter implementing the quote repository port."""

    def __init__(self) -> None:
        self._quotes: dict[str, ListingsResponse] = {}
        self._receipts: dict[str, PurchaseReceipt] = {}
        self._lock = Lock()

    def save(self, quote: ListingsResponse) -> None:
        with self._lock:
            if quote.quote_id in self._quotes:
                raise ValueError(f"duplicate quote id: {quote.quote_id}")
            self._quotes[quote.quote_id] = quote.model_copy(deep=True)

    def get(self, quote_id: str) -> ListingsResponse | None:
        with self._lock:
            quote = self._quotes.get(quote_id)
            return quote.model_copy(deep=True) if quote else None

    def delete_expired(self, before: datetime) -> int:
        with self._lock:
            expired_ids = [
                quote_id for quote_id, quote in self._quotes.items() if quote.expires_at <= before
            ]
            for quote_id in expired_ids:
                self._quotes.pop(quote_id, None)
                self._receipts.pop(quote_id, None)
            return len(expired_ids)

    def save_receipt_if_absent(self, receipt: PurchaseReceipt) -> PurchaseReceipt | None:
        with self._lock:
            existing = self._receipts.get(receipt.quote_id)
            if existing is not None:
                return existing.model_copy(deep=True)
            self._receipts[receipt.quote_id] = receipt.model_copy(deep=True)
            return None
