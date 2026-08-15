"""The HTTP surface.

Thin routers over the services, so these tests check the wiring rather than the
rules: that the right status codes come back, that strict validation actually
reaches the edge, and that a verdict posted over HTTP lands in the audit trail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trustrail.app import create_app
from trustrail.contracts.keys import ISSUER_ADDRESS, ISSUER_PRIVATE_KEY
from trustrail.contracts.scenarios import ScenarioBuilder, demo_config
from trustrail.mandate.service import MandateService
from trustrail.signing.local import LocalSigner
from trustrail.stores.memory import (
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
)
from trustrail.verifier.service import VerifierService

MINT_BODY = {
    "principal": "0x" + "11" * 20,
    "agent_id": "browser-1",
    "max_amount": {"currency": "XSGD", "amount": "5.00"},
    "intent": "toothbrush under $5",
    "ttl_seconds": 600,
}


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def client(audit_log: InMemoryAuditLog) -> TestClient:
    """An app signing with the same issuer key the fixtures were signed with."""
    mandates = MandateService(
        signer=LocalSigner(ISSUER_PRIVATE_KEY),
        store=InMemoryMandateStore(),
        kill_switch=InMemoryKillSwitchStore(),
        audit=audit_log,
    )
    return TestClient(
        create_app(mandates=mandates, verifier=VerifierService(demo_config()))
    )


def _mint(client: TestClient) -> dict:
    response = client.post("/mandates", json=MINT_BODY)
    assert response.status_code == 201
    return response.json()


def test_health_reports_the_issuer_the_verifier_must_trust(
    client: TestClient,
) -> None:
    body = client.get("/health").json()

    assert body == {"status": "ok", "issuer": ISSUER_ADDRESS}


def test_minting_returns_the_signed_mandate(client: TestClient) -> None:
    record = _mint(client)

    assert record["status"] == "MINTED"
    assert record["signed"]["mandate"]["max_amount"] == {
        "currency": "XSGD",
        "amount": "5.00",
    }
    assert record["signed"]["mandate"]["merchant_address"] is None


def test_a_missing_mandate_is_a_404(client: TestClient) -> None:
    assert client.get("/mandates/" + "0x" + "00" * 32).status_code == 404


def test_mandates_can_be_listed_for_a_buyer(client: TestClient) -> None:
    _mint(client)
    _mint(client)

    listed = client.get("/mandates", params={"principal": MINT_BODY["principal"]})

    assert len(listed.json()) == 2


def test_binding_then_consuming_walks_the_lifecycle(client: TestClient) -> None:
    mandate_id = _mint(client)["signed"]["mandate"]["mandate_id"]
    binding = {
        "binding": {
            "merchant_address": "0x" + "ab" * 20,
            "basket_hash": "0x" + "cd" * 32,
        },
        "approved_by": "ernest",
    }

    bound = client.post(f"/mandates/{mandate_id}/bind", json=binding)
    consumed = client.post(
        f"/mandates/{mandate_id}/consume", json={"actor": "worker", "reason": "settle"}
    )

    assert bound.json()["status"] == "BOUND"
    assert consumed.json()["status"] == "CONSUMED"


def test_a_second_consume_is_a_conflict_not_a_success(client: TestClient) -> None:
    """The HTTP shape of one-time consumption."""
    mandate_id = _mint(client)["signed"]["mandate"]["mandate_id"]
    body = {"actor": "worker", "reason": "settle"}
    client.post(f"/mandates/{mandate_id}/consume", json=body)

    second = client.post(f"/mandates/{mandate_id}/consume", json=body)

    assert second.status_code == 409
    assert second.json()["error"] == "MandateStatusConflict"


def test_binding_a_revoked_mandate_is_a_conflict(client: TestClient) -> None:
    mandate_id = _mint(client)["signed"]["mandate"]["mandate_id"]
    client.post(
        f"/mandates/{mandate_id}/revoke",
        json={"actor": "ernest", "reason": "changed my mind"},
    )

    response = client.post(
        f"/mandates/{mandate_id}/bind",
        json={
            "binding": {
                "merchant_address": "0x" + "ab" * 20,
                "basket_hash": "0x" + "cd" * 32,
            },
            "approved_by": "ernest",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"] == "IllegalBinding"


def test_the_audit_endpoint_returns_the_history(client: TestClient) -> None:
    mandate_id = _mint(client)["signed"]["mandate"]["mandate_id"]
    client.post(
        f"/mandates/{mandate_id}/revoke", json={"actor": "ernest", "reason": "no"}
    )

    events = [e["event_type"] for e in client.get(f"/mandates/{mandate_id}/audit").json()]

    assert events == ["MANDATE_MINTED", "MANDATE_REVOKED"]


def test_the_kill_switch_can_be_operated_over_http(client: TestClient) -> None:
    response = client.post("/kill-switch", json={"active": True, "actor": "ops"})

    assert response.status_code == 204


# --- strict validation at the edge -----------------------------------------


@pytest.mark.parametrize(
    "bad_field",
    [
        {"principal": "not-an-address"},
        {"max_amount": {"currency": "XSGD", "amount": "1.0000001"}},
        {"max_amount": {"currency": "DOGE", "amount": "1.00"}},
        {"ttl_seconds": 10},
        {"ttl_seconds": 999_999},
        {"intent": ""},
        {"surprise": "unexpected field"},
    ],
    ids=[
        "bad_address",
        "too_precise",
        "unknown_currency",
        "ttl_too_short",
        "ttl_too_long",
        "empty_intent",
        "extra_field",
    ],
)
def test_malformed_mint_requests_are_rejected(
    client: TestClient, bad_field: dict
) -> None:
    """Strict schemas, unexpected fields refused. Nothing gets in loosely typed."""
    assert client.post("/mandates", json=MINT_BODY | bad_field).status_code == 422


# --- verification ----------------------------------------------------------


def test_verify_returns_a_verdict_for_a_clean_charge(
    client: TestClient, build: ScenarioBuilder
) -> None:
    response = client.post(
        "/verify", json=build.request().model_dump(mode="json")
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "PASS"


def test_verify_reports_a_rejection_rather_than_erroring(
    client: TestClient, build: ScenarioBuilder
) -> None:
    """A rejected charge is a verdict to render, not an exception to swallow."""
    request = build.request(kill_switch_active=True)

    response = client.post("/verify", json=request.model_dump(mode="json"))

    assert response.status_code == 200
    assert response.json()["decision"] == "FAIL"
    assert response.json()["reason_codes"] == ["KILL_SWITCH_ACTIVE"]


def test_every_verdict_reaches_the_audit_trail(
    client: TestClient, build: ScenarioBuilder, audit_log: InMemoryAuditLog
) -> None:
    request = build.request(kill_switch_active=True)

    client.post("/verify", json=request.model_dump(mode="json"))

    entries = audit_log.list_for_mandate(request.signed_mandate.mandate.mandate_id)
    assert [e.event_type for e in entries] == ["VERDICT_ISSUED"]
    assert entries[0].verdict.decision == "FAIL"
