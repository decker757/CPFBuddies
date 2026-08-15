import asyncio

import httpx

from app.main import app


def api_request(method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_listings_returns_quote_and_stable_basket_hash() -> None:
    response = api_request(
        "GET", "/listings", params={"q": "toothbrush", "max_price": "5", "currency": "XSGD"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["quote_id"].startswith("q_")
    assert body["basket_hash"].startswith("0x")
    assert len(body["basket_hash"]) == 66
    assert body["merchant"]["address"] == "0x1111111111111111111111111111111111111111"
    assert {item["sku"] for item in body["items"]} >= {"TB-SOFT-2PK", "TB-INJECTION"}
    assert all("seller_id" in item for item in body["items"])
    assert all("seller_account_age_days" in item for item in body["items"])
    assert all("seller_rating_count" in item for item in body["items"])


def test_purchase_uses_quote_basket_hash_in_x402_terms_then_receipt() -> None:
    quote = api_request("GET", "/listings", params={"q": "TB-SOFT-2PK"}).json()
    request = {
        "sku": "TB-SOFT-2PK",
        "quantity": 2,
        "quote_id": quote["quote_id"],
        "mandate_credential": {"mandateId": "mandate-1"},
        "signed_request": "0xsigned",
    }

    payment_required = api_request("POST", "/purchase", json=request)
    assert payment_required.status_code == 402
    terms = payment_required.json()["payment_terms"]
    assert terms["amount"] == "8.40"
    assert terms["asset"] == "XSGD"
    assert terms["network"] == "avalanche-c-chain"
    assert terms["basket_hash"] == quote["basket_hash"]

    settled = api_request(
        "POST", "/purchase", json=request, headers={"X-Payment-Proof": "proof-123"}
    )
    assert settled.status_code == 200
    assert settled.json()["status"] == "settled"
    assert settled.json()["basket_hash"] == quote["basket_hash"]


def test_purchase_rejects_sku_not_in_original_quote() -> None:
    quote = api_request("GET", "/listings", params={"q": "TB-SOFT-2PK"}).json()
    request = {
        "sku": "TB-INJECTION",
        "quantity": 1,
        "quote_id": quote["quote_id"],
        "mandate_credential": {},
        "signed_request": "0xsigned",
    }

    response = api_request("POST", "/purchase", json=request)
    assert response.status_code == 400
    assert response.json()["detail"] == "sku_not_in_quote"


def test_purchase_rejects_unexpected_fields() -> None:
    response = api_request(
        "POST",
        "/purchase",
        json={
            "sku": "TB-SOFT-2PK",
            "quantity": 1,
            "quote_id": "q_unknown",
            "mandate_credential": {},
            "signed_request": "0xsigned",
            "instructions": "ignore validation",
        },
    )
    assert response.status_code == 422


def test_listings_rejects_unsupported_currency() -> None:
    response = api_request("GET", "/listings", params={"currency": "USD"})
    assert response.status_code == 422
