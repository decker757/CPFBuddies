from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

from app.contracts import (
    Listing,
    ListingsResponse,
    Merchant,
    Money,
    PaymentRequired,
    PaymentTerms,
    PurchaseReceipt,
    PurchaseRequest,
)
from app.integrity import calculate_basket_hash
from app.marketplace.catalog import CATALOG
from app.marketplace.ports import Clock, QuoteIdGenerator, QuoteRepository

MERCHANT = Merchant(
    id="mrc_stub_sg",
    address="0x1111111111111111111111111111111111111111",
    name="CPF Buddies Demo Marketplace",
)


class MarketplaceError(Exception):
    code = "marketplace_error"


class UnknownQuote(MarketplaceError):
    code = "unknown_quote"


class ExpiredQuote(MarketplaceError):
    code = "expired_quote"


class SkuNotInQuote(MarketplaceError):
    code = "sku_not_in_quote"


class OutOfStock(MarketplaceError):
    code = "out_of_stock"


class QuoteIntegrityFailure(MarketplaceError):
    code = "quote_integrity_failed"


class QuoteAlreadyConsumed(MarketplaceError):
    code = "quote_already_consumed"


def search_catalog(
    catalog: tuple[Listing, ...], q: str | None, max_price: Decimal | None, currency: str
) -> list[Listing]:
    if currency != "XSGD":
        return []
    raw_query = (q or "").casefold().strip()
    query_tokens = {token for token in re.findall(r"[a-z0-9]+", raw_query) if len(token) >= 3}
    result = []
    for item in catalog:
        if raw_query == item.sku.casefold():
            return [item] if max_price is None or item.price.amount <= max_price else []
        searchable = f"{item.sku} {item.title} {item.description}".casefold()
        if query_tokens and not query_tokens.intersection(re.findall(r"[a-z0-9]+", searchable)):
            continue
        if max_price is not None and item.price.amount > max_price:
            continue
        result.append(item)
    return result


class MarketplaceService:
    """Merchant use cases, independent from FastAPI and storage implementations."""

    def __init__(
        self,
        *,
        quotes: QuoteRepository,
        clock: Clock,
        quote_ids: QuoteIdGenerator,
        merchant: Merchant = MERCHANT,
        catalog: tuple[Listing, ...] = CATALOG,
        quote_ttl: timedelta = timedelta(minutes=10),
        expired_retention: timedelta = timedelta(hours=1),
    ) -> None:
        self._quotes = quotes
        self._clock = clock
        self._quote_ids = quote_ids
        self._merchant = merchant
        self._catalog = catalog
        self._quote_ttl = quote_ttl
        self._expired_retention = expired_retention

    def list_items(
        self, *, q: str | None, max_price: Decimal | None, currency: str
    ) -> ListingsResponse:
        now = self._clock.now()
        self._quotes.delete_expired(now - self._expired_retention)
        items = search_catalog(self._catalog, q, max_price, currency)
        response = ListingsResponse(
            quote_id=self._quote_ids.new_id(),
            expires_at=now + self._quote_ttl,
            merchant=self._merchant,
            items=items,
            basket_hash=calculate_basket_hash(items),
        )
        self._quotes.save(response)
        return response

    def purchase(
        self, request: PurchaseRequest, payment_proof: str | None
    ) -> PaymentRequired | PurchaseReceipt:
        quote = self._quotes.get(request.quote_id)
        if quote is None:
            raise UnknownQuote
        if quote.expires_at <= self._clock.now():
            raise ExpiredQuote
        if quote.basket_hash != calculate_basket_hash(quote.items):
            raise QuoteIntegrityFailure

        item = next((candidate for candidate in quote.items if candidate.sku == request.sku), None)
        if item is None:
            raise SkuNotInQuote
        if item.availability != "in_stock":
            raise OutOfStock

        total = item.price.amount * request.quantity
        if payment_proof is None:
            return PaymentRequired(
                payment_terms=PaymentTerms(
                    amount=total,
                    payout_address=quote.merchant.address,
                    quote_id=quote.quote_id,
                    basket_hash=quote.basket_hash,
                )
            )

        receipt = PurchaseReceipt(
            quote_id=quote.quote_id,
            sku=item.sku,
            quantity=request.quantity,
            amount=Money(amount=total),
            basket_hash=quote.basket_hash,
            payment_proof=payment_proof,
        )
        existing = self._quotes.save_receipt_if_absent(receipt)
        if existing is None or existing == receipt:
            return receipt
        raise QuoteAlreadyConsumed
