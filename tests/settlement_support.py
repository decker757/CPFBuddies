"""Helpers shared by the settlement tests.

The important one is :func:`settlement_request`: it produces a queue payload by running
workstream A's real Verifier over a real scenario, rather than fabricating a verdict. That way
these tests fail if the two workstreams stop agreeing, which is the whole point of having a
wire contract.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from trustrail.contracts.keys import label_to_hash
from trustrail.contracts.scenarios import ScenarioBuilder
from trustrail.settlement.models import SettlementRequest
from trustrail.verifier.service import VerifierService

# Hardhat's deterministic development accounts. Public knowledge, worthless, local only.
HARDHAT_ACCOUNTS = {
    "deployer": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "principal": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "merchant": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
}

LOCAL_RPC = "http://127.0.0.1:8545"


def settlement_request(
    build: ScenarioBuilder, verifier: VerifierService, **overrides
) -> SettlementRequest:
    """A queue payload whose verdict came from the real Verifier."""
    request = build.request(**overrides)
    return SettlementRequest(
        verdict=verifier.verify(request),
        charge=request.charge,
        signed_mandate=request.signed_mandate,
    )


def live_overrides(
    build: ScenarioBuilder,
    label: str,
    *,
    principal: str | None = None,
    payout_address: str | None = None,
    window: timedelta = timedelta(hours=1),
) -> dict:
    """Scenario overrides pinned to real wall-clock time and real chain addresses.

    The committed scenarios use a fixed demo timestamp, which is fine for a pure Verifier test
    but not for the contract: it checks expiry against block time.

    Mandate ids are made unique per call rather than derived from ``label`` alone. A mandate is
    one-shot and a local node keeps its state between runs, so a deterministic id would collide
    with itself the second time the suite is run against the same chain.
    """
    now = datetime.now(timezone.utc)
    mandate_id = label_to_hash(f"mandate:{label}:{time.time_ns()}")

    mandate_fields: dict = {"mandate_id": mandate_id, "expires_at": now + window}
    if principal is not None:
        mandate_fields["principal"] = principal

    charge_fields: dict = {"mandate_id": mandate_id}
    if payout_address is not None:
        charge_fields["payout_address"] = payout_address

    charge = build.charge(**charge_fields)
    overrides: dict = {
        "signed_mandate": build.sign_mandate(build.mandate(**mandate_fields)),
        "charge": charge,
        "evaluation": build.sign_evaluation(build.evaluation(charge)),
        "now": now,
    }
    if payout_address is not None:
        overrides["merchant"] = build.merchant(payout_address=payout_address)
    return overrides
