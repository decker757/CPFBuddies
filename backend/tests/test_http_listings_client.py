import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.agents.browser import HttpListingsClient
from app.contracts import ListingsResponse
from app.integrity import calculate_basket_hash
from app.marketplace.catalog import CATALOG
from app.marketplace.service import MERCHANT


def valid_payload() -> dict:
    response = ListingsResponse(
        quote_id="q_http_test",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        merchant=MERCHANT,
        items=[CATALOG[0]],
        basket_hash=calculate_basket_hash([CATALOG[0]]),
    )
    return response.model_dump(mode="json")


def fetch_payload(payload: dict) -> ListingsResponse:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    async def fetch() -> ListingsResponse:
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = HttpListingsClient("https://merchant.example", client=client)
            return await adapter.fetch_listings(q="toothbrush", max_price=5)

    return asyncio.run(fetch())


def test_http_client_accepts_contract_compliant_response() -> None:
    assert fetch_payload(valid_payload()).quote_id == "q_http_test"


@pytest.mark.parametrize("url", ["ftp://merchant.example", "https://user:secret@merchant.example"])
def test_http_client_rejects_unsafe_base_url(url: str) -> None:
    with pytest.raises(ValueError):
        HttpListingsClient(url)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": "field"}),
        lambda payload: payload["items"][0].update({"description": "x" * 4_001}),
        lambda payload: payload.update({"basket_hash": "not-a-hash"}),
    ],
)
def test_http_client_rejects_malformed_or_oversized_response(mutate) -> None:
    payload = valid_payload()
    mutate(payload)
    with pytest.raises(ValidationError):
        fetch_payload(payload)
