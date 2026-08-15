import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.agents.browser import BrowserAgent, NoCandidateFound
from app.contracts import ListingsResponse
from app.integrity import calculate_basket_hash
from app.marketplace.catalog import CATALOG
from app.marketplace.service import MERCHANT


class FakeListingsClient:
    def __init__(self, items):
        self.items = items
        self.last_query = None

    async def fetch_listings(self, *, q, max_price, currency="XSGD"):
        self.last_query = {"q": q, "max_price": max_price, "currency": currency}
        return ListingsResponse(
            quote_id="q_test",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            merchant=MERCHANT,
            items=self.items,
            basket_hash=calculate_basket_hash(self.items),
        )


class FailingListingsClient:
    async def fetch_listings(self, **kwargs):
        del kwargs
        raise httpx.ConnectError("merchant unavailable")


class SlowListingsClient:
    async def fetch_listings(self, **kwargs):
        del kwargs
        await asyncio.sleep(0.05)
        raise AssertionError("request should have timed out")


def test_browser_preserves_quote_binding_in_candidate() -> None:
    items = list(CATALOG[:2])
    client = FakeListingsClient(items)
    candidate = asyncio.run(
        BrowserAgent([client]).find_candidate(
            intent="toothbrush", max_price=Decimal("5"), preferred_sku="TB-INJECTION"
        )
    )
    assert candidate.quote_id == "q_test"
    assert candidate.listing.sku == "TB-INJECTION"
    assert candidate.basket_hash == calculate_basket_hash(items)
    assert client.last_query == {"q": "toothbrush", "max_price": Decimal("5"), "currency": "XSGD"}


def test_browser_fails_closed_when_preferred_candidate_is_absent() -> None:
    client = FakeListingsClient(list(CATALOG[:1]))
    try:
        asyncio.run(
            BrowserAgent([client]).find_candidate(
                intent="toothbrush", max_price=Decimal("5"), preferred_sku="missing"
            )
        )
    except NoCandidateFound:
        pass
    else:
        raise AssertionError("missing preferred SKU must fail closed")


def test_browser_isolates_failed_merchants() -> None:
    healthy = FakeListingsClient(list(CATALOG[:1]))
    candidate = asyncio.run(
        BrowserAgent([FailingListingsClient(), healthy]).find_candidate(
            intent="toothbrush", max_price=Decimal("5")
        )
    )
    assert candidate.listing.sku == "TB-SOFT-2PK"


def test_browser_rejects_mismatched_basket_hash() -> None:
    class TamperedClient(FakeListingsClient):
        async def fetch_listings(self, **kwargs):
            response = await super().fetch_listings(**kwargs)
            return response.model_copy(update={"basket_hash": f"0x{'0' * 64}"})

    try:
        asyncio.run(
            BrowserAgent([TamperedClient(list(CATALOG[:1]))]).find_candidate(
                intent="toothbrush", max_price=Decimal("5")
            )
        )
    except NoCandidateFound:
        pass
    else:
        raise AssertionError("tampered quote must fail closed")


def test_browser_times_out_slow_merchant_but_uses_healthy_one() -> None:
    healthy = FakeListingsClient(list(CATALOG[:1]))
    candidate = asyncio.run(
        BrowserAgent([SlowListingsClient(), healthy], request_timeout=0.001).find_candidate(
            intent="toothbrush", max_price=Decimal("5")
        )
    )
    assert candidate.listing.sku == "TB-SOFT-2PK"


def test_browser_selection_is_deterministic_across_merchants() -> None:
    expensive = FakeListingsClient(list(CATALOG[:1]))
    cheap = FakeListingsClient(list(CATALOG[3:4]))
    candidate = asyncio.run(
        BrowserAgent([expensive, cheap]).find_candidate(intent="toothbrush", max_price=Decimal("5"))
    )
    assert candidate.listing.sku == "TB-SUSPICIOUS"


def test_browser_rejects_invalid_intent_before_calling_merchant() -> None:
    client = FakeListingsClient(list(CATALOG[:1]))
    try:
        asyncio.run(BrowserAgent([client]).find_candidate(intent=" ", max_price=Decimal("5")))
    except ValueError:
        pass
    else:
        raise AssertionError("blank intent must be rejected")
    assert client.last_query is None
