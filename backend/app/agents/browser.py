from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

import httpx

from app.contracts import CandidateSelection, Listing, ListingsResponse
from app.integrity import calculate_basket_hash

logger = logging.getLogger(__name__)


class ListingsClient(Protocol):
    async def fetch_listings(
        self, *, q: str, max_price: Decimal, currency: str = "XSGD"
    ) -> ListingsResponse: ...


class HttpListingsClient:
    """HTTP adapter for any merchant implementing the two-endpoint contract."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        parsed_url = httpx.URL(base_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.userinfo:
            raise ValueError("merchant base URL must be HTTP(S) without embedded credentials")
        self._base_url = str(parsed_url).rstrip("/")
        self._client = client

    async def fetch_listings(
        self, *, q: str, max_price: Decimal, currency: str = "XSGD"
    ) -> ListingsResponse:
        params = {"q": q, "max_price": str(max_price), "currency": currency}
        if self._client is not None:
            response = await self._client.get(f"{self._base_url}/listings", params=params)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self._base_url}/listings", params=params)
        response.raise_for_status()
        return ListingsResponse.model_validate(response.json())


class NoCandidateFound(LookupError):
    pass


class BrowserAgent:
    """Queries merchants concurrently and makes a deterministic candidate choice."""

    def __init__(
        self,
        listings_clients: Sequence[ListingsClient],
        *,
        request_timeout: float = 5.0,
        max_concurrency: int = 4,
    ) -> None:
        if not listings_clients:
            raise ValueError("at least one listings client is required")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        self._listings_clients = tuple(listings_clients)
        self._request_timeout = request_timeout
        self._max_concurrency = max_concurrency

    async def find_candidate(
        self,
        *,
        intent: str,
        max_price: Decimal,
        preferred_sku: str | None = None,
    ) -> CandidateSelection:
        normalized_intent = intent.strip()
        if not normalized_intent or len(normalized_intent) > 200:
            raise ValueError("intent must contain between 1 and 200 characters")
        if max_price <= 0:
            raise ValueError("max_price must be positive")
        responses = await self._fetch_all(intent=normalized_intent, max_price=max_price)
        candidates = [
            (response, item)
            for response in responses
            for item in response.items
            if item.availability == "in_stock" and item.price.amount <= max_price
        ]
        response, selected = self._select(candidates, preferred_sku)
        return CandidateSelection(
            quote_id=response.quote_id,
            expires_at=response.expires_at,
            merchant=response.merchant,
            listing=selected,
            basket_hash=response.basket_hash,
        )

    async def _fetch_all(self, *, intent: str, max_price: Decimal) -> list[ListingsResponse]:
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def fetch(client: ListingsClient) -> ListingsResponse | None:
            try:
                async with semaphore:
                    response = await asyncio.wait_for(
                        client.fetch_listings(q=intent, max_price=max_price),
                        timeout=self._request_timeout,
                    )
                if response.expires_at <= datetime.now(UTC):
                    logger.warning("merchant quote rejected", extra={"reason": "expired_quote"})
                    return None
                if response.basket_hash != calculate_basket_hash(response.items):
                    logger.warning(
                        "merchant quote rejected", extra={"reason": "basket_hash_mismatch"}
                    )
                    return None
                return response
            except Exception as error:
                logger.warning(
                    "merchant listing request failed",
                    extra={"error_type": type(error).__name__},
                )
                return None

        results = await asyncio.gather(*(fetch(client) for client in self._listings_clients))
        responses = [response for response in results if response is not None]
        if not responses:
            raise NoCandidateFound(
                "all merchant listing requests failed or returned expired quotes"
            )
        return responses

    @staticmethod
    def _select(
        candidates: list[tuple[ListingsResponse, Listing]], preferred_sku: str | None
    ) -> tuple[ListingsResponse, Listing]:
        if preferred_sku is not None:
            selected = next(
                (candidate for candidate in candidates if candidate[1].sku == preferred_sku),
                None,
            )
            if selected is None:
                raise NoCandidateFound(f"preferred SKU {preferred_sku!r} is unavailable")
            return selected
        if not candidates:
            raise NoCandidateFound("merchant returned no in-stock candidate")
        return min(
            candidates,
            key=lambda candidate: (
                candidate[1].price.amount,
                candidate[0].merchant.id,
                candidate[1].sku,
            ),
        )
