import asyncio
from datetime import UTC, datetime

import httpx
from trustrail.x402.terms import (
    PAYMENT_HEADER,
    SCHEME,
    PaymentProof,
    encode_proof,
    parse_terms,
)

from app.contracts import xsgd
from app.main import app
from app.marketplace.service import MERCHANT


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
    # Asserted against MERCHANT rather than a literal: this checks that the
    # listing publishes the platform's own address, which is what the Verifier
    # compares to the Merchant Registry. Pinning the value here instead would
    # mean changing the demo payout address breaks a test that has no opinion
    # about what the address is.
    assert body["merchant"]["address"] == MERCHANT.address.lower()
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

    # The 402 body must be readable by workstream C's client, not merely by us. Parsing it
    # through C's own model is the check that matters: if the two drift, this fails here
    # rather than at the demo.
    terms = parse_terms(payment_required.json()["payment_terms"])
    assert terms.amount == xsgd("8.40")
    assert terms.scheme == SCHEME
    assert terms.basket_hash == quote["basket_hash"]
    assert terms.pay_to == quote["merchant"]["address"]
    assert terms.chain_id == 43113
    assert not terms.is_expired(datetime.now(UTC))

    proof = PaymentProof(
        quote_id=terms.quote_id,
        basket_hash=terms.basket_hash,
        mandate_id="0x" + "11" * 32,
        amount=terms.amount,
        payer="0x" + "22" * 20,
        reference="0x" + "7e" * 32,
        settled_at=datetime.now(UTC),
    )
    settled = api_request(
        "POST",
        "/purchase",
        json=request,
        headers={PAYMENT_HEADER: encode_proof(proof)},
    )
    assert settled.status_code == 200
    assert settled.json()["status"] == "settled"
    assert settled.json()["basket_hash"] == quote["basket_hash"]
    # The receipt records the rail's reference, taken from the proof we sent.
    assert settled.json()["payment_proof"] == proof.reference


def test_purchase_rejects_a_malformed_payment_proof() -> None:
    quote = api_request("GET", "/listings", params={"q": "TB-SOFT-2PK"}).json()
    request = {
        "sku": "TB-SOFT-2PK",
        "quantity": 1,
        "quote_id": quote["quote_id"],
        "mandate_credential": {},
        "signed_request": "0xsigned",
    }

    response = api_request(
        "POST", "/purchase", json=request, headers={PAYMENT_HEADER: "not-a-proof"}
    )

    # A receipt issued against a proof we could not read would be a receipt for nothing.
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_payment_proof"


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
