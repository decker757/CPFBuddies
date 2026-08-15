from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.contracts import (
    Listing,
    ListingsResponse,
    Merchant,
    PaymentRequired,
    PurchaseReceipt,
    PurchaseRequest,
    xsgd,
)
from app.integrity import calculate_basket_hash
from app.marketplace.catalog import CATALOG
from app.marketplace.ports import Clock, QuoteIdGenerator, QuoteRepository
from trustrail.models.money import Currency, Money
from trustrail.signing.crypto import hash_bytes
from trustrail.x402.terms import SCHEME, PaymentTerms

MERCHANT = Merchant(
    id="mrc_stub_sg",
    address="0x1111111111111111111111111111111111111111",
    name="CPF Buddies Demo Marketplace",
)

#: Length of the generated terms nonce. `PaymentTerms.nonce` is ShortText (64 characters), and
#: 32 hex characters is comfortably unique for one quote's lifetime.
_NONCE_HEX_CHARS = 32


@dataclass(frozen=True)
class SettlementProfile:
    """Which chain and which token a 402 from this marketplace refers to.

    Carried in the payment terms rather than assumed by the client, so the same merchant can
    quote against Fuji today and mainnet tomorrow without the buyer guessing. Defaults are
    Fuji; a deployment overrides them from C's `onchain/deployments/<network>.json`.
    """

    chain_id: int = 43113
    network: str = "avalanche-fuji"
    asset: str = "0x0000000000000000000000000000000000000000"


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
    # Compared as exact minor units, like every other price comparison in the system.
    ceiling = xsgd(max_price).minor_units if max_price is not None else None
    raw_query = (q or "").casefold().strip()
    query_tokens = {token for token in re.findall(r"[a-z0-9]+", raw_query) if len(token) >= 3}
    result = []
    for item in catalog:
        if raw_query == item.sku.casefold():
            return [item] if ceiling is None or item.price.minor_units <= ceiling else []
        searchable = f"{item.sku} {item.title} {item.description}".casefold()
        if query_tokens and not query_tokens.intersection(re.findall(r"[a-z0-9]+", searchable)):
            continue
        if ceiling is not None and item.price.minor_units > ceiling:
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
        settlement: SettlementProfile = SettlementProfile(),
        catalog: tuple[Listing, ...] = CATALOG,
        quote_ttl: timedelta = timedelta(minutes=10),
        expired_retention: timedelta = timedelta(hours=1),
    ) -> None:
        self._quotes = quotes
        self._clock = clock
        self._quote_ids = quote_ids
        self._merchant = merchant
        self._settlement = settlement
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

        # Exact integer arithmetic: a quantity multiplier must not introduce rounding.
        total = Money.from_minor_units(
            item.price.minor_units * request.quantity, Currency.XSGD
        )
        if payment_proof is None:
            return PaymentRequired(
                payment_terms=PaymentTerms(
                    scheme=SCHEME,
                    network=self._settlement.network,
                    chain_id=self._settlement.chain_id,
                    asset=self._settlement.asset,
                    pay_to=quote.merchant.address,
                    amount=total,
                    basket_hash=quote.basket_hash,
                    quote_id=quote.quote_id,
                    # Terms cannot outlive the quote they price.
                    expires_at=quote.expires_at,
                    nonce=_terms_nonce(quote.quote_id, item.sku, request.quantity),
                )
            )

        receipt = PurchaseReceipt(
            quote_id=quote.quote_id,
            sku=item.sku,
            quantity=request.quantity,
            amount=total,
            basket_hash=quote.basket_hash,
            payment_proof=payment_proof,
        )
        existing = self._quotes.save_receipt_if_absent(receipt)
        if existing is None or existing == receipt:
            return receipt
        raise QuoteAlreadyConsumed


def _terms_nonce(quote_id: str, sku: str, quantity: int) -> str:
    """A nonce that is stable for one (quote, sku, quantity) and unique across them.

    Deterministic on purpose: a client that retries the same purchase gets the same terms
    rather than a fresh nonce each time, and the tests do not need a clock or a random source.
    """
    seed = f"{quote_id}:{sku}:{quantity}".encode()
    return hash_bytes(seed)[2 : 2 + _NONCE_HEX_CHARS]
