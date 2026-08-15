"""Wiring the rail up: the preflight checks, CORS, and the two ASGI factories.

None of this is business logic, and all of it is the kind of thing that fails at
3am in a way that looks like something else. A wrong role reverts three steps
later; a wrong signing domain does not fail at all; a missing CORS header fails
only in a browser, which is the one place nobody is running the test suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.rail import (
    _cors_origins_from_env,
    _preferred_sku_from_env,
    build_rail,
    chain_app,
)
from trustrail.settlement.chain.deployment import Deployment
from trustrail.settlement.wiring import check_deployment
from trustrail.signing.eip712 import Eip712Domain

REGISTRAR = "0x" + "11" * 20
SETTLER = "0x" + "22" * 20
REGISTRY = "0x" + "33" * 20
CHAIN_ID = 43114


def _deployment(**overrides) -> Deployment:
    fields = {
        "network": "avalanche",
        "chainId": CHAIN_ID,
        "settlementToken": "0x" + "44" * 20,
        "settlementTokenSymbol": "XSGD",
        "settlementTokenDecimals": 6,
        "mandateRegistry": REGISTRY,
        "registrar": REGISTRAR,
        "settler": SETTLER,
    }
    return Deployment.model_validate(fields | overrides)


def _domain(**overrides) -> Eip712Domain:
    fields = {"chain_id": CHAIN_ID, "verifying_contract": REGISTRY}
    return Eip712Domain(**(fields | overrides))


def _problems(deployment=None, domain=None, *, registrar=REGISTRAR, settler=SETTLER):
    return check_deployment(
        deployment or _deployment(),
        registrar_address=registrar,
        settler_address=settler,
        domain=domain or _domain(),
    )


class TestDeploymentPreflight:
    def test_a_matching_deployment_has_nothing_to_say(self):
        assert _problems() == []

    def test_case_does_not_count_as_a_mismatch(self):
        """Checksummed and lowercase addresses are the same address."""
        assert _problems(registrar=REGISTRAR.upper(), settler=SETTLER.upper()) == []

    def test_the_wrong_registrar_is_caught_before_anything_mints(self):
        [problem] = _problems(registrar="0x" + "99" * 20)

        assert "REGISTRAR_ROLE" in problem
        assert "TRUSTRAIL_REGISTRAR_KEY" in problem

    def test_the_wrong_settler_is_caught_before_anything_settles(self):
        [problem] = _problems(settler="0x" + "99" * 20)

        assert "SETTLER_ROLE" in problem

    def test_a_domain_on_the_wrong_chain_is_refused(self):
        [problem] = _problems(domain=_domain(chain_id=1))

        assert "chain 1" in problem

    def test_a_domain_naming_the_wrong_registry_is_refused(self):
        """The silent one: mandates still verify while the digests mean nothing."""
        [problem] = _problems(domain=_domain(verifying_contract="0x" + "99" * 20))

        assert "verifying contract" in problem

    def test_every_problem_is_reported_not_just_the_first(self):
        """Fixing these one restart at a time is how an evening disappears."""
        problems = _problems(
            registrar="0x" + "99" * 20,
            settler="0x" + "88" * 20,
            domain=_domain(chain_id=1, verifying_contract="0x" + "77" * 20),
        )

        assert len(problems) == 4


class TestCorsOrigins:
    def test_the_dev_server_is_allowed_by_default(self, monkeypatch):
        monkeypatch.delenv("TRUSTRAIL_CORS_ORIGINS", raising=False)

        assert "http://localhost:5173" in _cors_origins_from_env()

    def test_origins_can_be_listed_in_the_environment(self, monkeypatch):
        monkeypatch.setenv("TRUSTRAIL_CORS_ORIGINS", "https://a.example, https://b.example")

        assert _cors_origins_from_env() == ["https://a.example", "https://b.example"]

    def test_an_empty_setting_allows_nothing(self, monkeypatch):
        """Explicitly locking the API down must not fall back to the defaults."""
        monkeypatch.setenv("TRUSTRAIL_CORS_ORIGINS", "")

        assert _cors_origins_from_env() == []

    def test_a_named_origin_gets_the_header(self):
        client = TestClient(build_rail(cors_origins=["http://localhost:5173"]).app)

        response = client.get("/audit", headers={"Origin": "http://localhost:5173"})

        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_an_unnamed_origin_does_not(self):
        client = TestClient(build_rail(cors_origins=["http://localhost:5173"]).app)

        response = client.get("/audit", headers={"Origin": "http://evil.example"})

        assert "access-control-allow-origin" not in response.headers

    def test_the_reconnect_header_survives_preflight(self):
        """`Last-Event-ID` is how the dashboard resumes; a blocked preflight
        turns every reconnect into a replay from zero."""
        client = TestClient(build_rail(cors_origins=["http://localhost:5173"]).app)

        response = client.options(
            "/audit/stream",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Last-Event-ID",
            },
        )

        assert response.status_code == 200
        allowed = response.headers["access-control-allow-headers"].lower()
        assert "last-event-id" in allowed


class TestDemoSku:
    def test_selection_is_free_when_unset(self, monkeypatch):
        """The honest default: the agent picks by price, and REVIEW wins."""
        monkeypatch.delenv("TRUSTRAIL_DEMO_SKU", raising=False)

        assert _preferred_sku_from_env() is None

    def test_an_empty_value_is_not_a_sku(self, monkeypatch):
        """An unset-looking env var must not pin the agent to the empty string,
        which the Browser Agent would reject as an unavailable listing."""
        monkeypatch.setenv("TRUSTRAIL_DEMO_SKU", "")

        assert _preferred_sku_from_env() is None

    def test_a_sku_pins_the_agent(self, monkeypatch):
        monkeypatch.setenv("TRUSTRAIL_DEMO_SKU", "TB-SOFT-2PK")

        assert _preferred_sku_from_env() == "TB-SOFT-2PK"


class TestChainApp:
    def test_it_refuses_to_start_without_a_registrar_key(self, monkeypatch):
        """It settles real money and will not invent a key to do it with."""
        monkeypatch.delenv("TRUSTRAIL_REGISTRAR_KEY", raising=False)
        monkeypatch.delenv("TRUSTRAIL_SETTLER_KEY", raising=False)

        with pytest.raises(RuntimeError, match="TRUSTRAIL_REGISTRAR_KEY"):
            chain_app()

    def test_it_refuses_to_start_without_a_settler_key(self, monkeypatch):
        """Separate keys are the point: one must not stand in for the other."""
        monkeypatch.setenv("TRUSTRAIL_REGISTRAR_KEY", "0x" + "44" * 32)
        monkeypatch.delenv("TRUSTRAIL_SETTLER_KEY", raising=False)

        with pytest.raises(RuntimeError, match="TRUSTRAIL_SETTLER_KEY"):
            chain_app()


class TestDemoAppIsHonestlyOffline:
    def test_no_chain_means_nothing_settles(self):
        """`demo_app` fills the queue and executes none of it, on purpose."""
        rail = build_rail()

        assert rail.chain is None
        assert rail.worker is None
        assert rail.settle_pending() == []
