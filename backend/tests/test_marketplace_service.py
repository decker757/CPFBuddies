from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import ListingsResponse, PurchaseRequest
from app.marketplace.repository import InMemoryQuoteRepository
from app.marketplace.service import (
    ExpiredQuote,
    MarketplaceService,
    QuoteAlreadyConsumed,
    QuoteIntegrityFailure,
)


class FixedClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class SequentialIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"q_test_{self.value}"


def make_service(
    repository: InMemoryQuoteRepository | None = None,
) -> tuple[MarketplaceService, InMemoryQuoteRepository, FixedClock]:
    quotes = repository or InMemoryQuoteRepository()
    clock = FixedClock()
    return (
        MarketplaceService(quotes=quotes, clock=clock, quote_ids=SequentialIds()),
        quotes,
        clock,
    )


def purchase_request(quote_id: str, sku: str = "TB-SOFT-2PK") -> PurchaseRequest:
    return PurchaseRequest(
        sku=sku,
        quantity=1,
        quote_id=quote_id,
        mandate_credential={"mandateId": "mandate-1"},
        signed_request="0xsigned",
    )


def test_expired_quote_is_rejected() -> None:
    service, _, clock = make_service()
    quote = service.list_items(q="TB-SOFT-2PK", max_price=Decimal("5"), currency="XSGD")
    clock.current += timedelta(minutes=11)

    with pytest.raises(ExpiredQuote):
        service.purchase(purchase_request(quote.quote_id), payment_proof=None)


def test_old_expired_quotes_are_cleaned_up() -> None:
    service, repository, clock = make_service()
    old_quote = service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    clock.current += timedelta(hours=2)

    service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    assert repository.get(old_quote.quote_id) is None


def test_payment_retry_is_idempotent_but_different_proof_is_rejected() -> None:
    service, _, _ = make_service()
    quote = service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    request = purchase_request(quote.quote_id)

    first = service.purchase(request, payment_proof="proof-1")
    retry = service.purchase(request, payment_proof="proof-1")
    assert retry == first

    with pytest.raises(QuoteAlreadyConsumed):
        service.purchase(request, payment_proof="proof-2")


def test_stored_quote_with_tampered_basket_hash_is_rejected() -> None:
    service, repository, _ = make_service()
    quote = service.list_items(q="TB-SOFT-2PK", max_price=None, currency="XSGD")
    corrupt = quote.model_copy(update={"quote_id": "q_corrupt", "basket_hash": f"0x{'0' * 64}"})
    repository.save(ListingsResponse.model_validate(corrupt))

    with pytest.raises(QuoteIntegrityFailure):
        service.purchase(purchase_request("q_corrupt"), payment_proof=None)


def test_empty_catalog_still_returns_a_bound_quote() -> None:
    quotes = InMemoryQuoteRepository()
    service = MarketplaceService(
        quotes=quotes,
        clock=FixedClock(),
        quote_ids=SequentialIds(),
        catalog=(),
    )
    quote = service.list_items(q="anything", max_price=None, currency="XSGD")
    assert quote.items == []
    assert quote.basket_hash.startswith("0x")
