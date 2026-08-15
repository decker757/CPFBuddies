"""End-to-end settlement against a real chain.

Runs the path CLAUDE.md's demo runs: workstream A's Verifier returns PASS, the request goes on
the queue, the worker picks a rail, and the contract either transfers XSGD or reverts.

Both halves matter. The happy path proves money moves; the refusals prove it cannot move
outside a mandate even when something upstream waves a bad charge through. The refusal tests
work by registering the mandate onchain with *stricter* terms than the offchain record -- the
Verifier passes, and the contract still says no. That is the "you do not have to trust our
services" claim, executed.

Requires:
    cd onchain && npx hardhat node
    cd onchain && npm run deploy:local
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hexbytes import HexBytes
from web3 import Web3

from trustrail.clock import SystemClock
from trustrail.models.audit import SettlementOutcome
from trustrail.models.money import Currency, Money
from trustrail.models.verdict import Decision, ReasonCode
from trustrail.settlement.chain.deployment import load_abi, load_deployment
from trustrail.settlement.chain.registry_client import MandateRegistryClient
from trustrail.settlement.chain.transactions import (
    build_transaction,
    send_raw_transaction,
    sign_transaction,
)
from trustrail.settlement.queue.memory import InMemorySettlementQueue
from trustrail.settlement.rails.x402_onchain import RAIL_NAME, X402OnchainRail
from trustrail.settlement.worker import SettlementWorker
from trustrail.signing.local import LocalSigner
from trustrail.stores.memory import InMemoryAuditLog
from tests.settlement_support import HARDHAT_ACCOUNTS, LOCAL_RPC, live_overrides, settlement_request

pytestmark = pytest.mark.integration

FUNDING = 1_000_000_000  # minor units minted to the principal before each run


@pytest.fixture(scope="module")
def w3() -> Web3:
    connection = Web3(Web3.HTTPProvider(LOCAL_RPC, request_kwargs={"timeout": 5}))
    if not connection.is_connected():
        pytest.skip(f"no chain at {LOCAL_RPC}; run 'npx hardhat node' in onchain/")
    return connection


@pytest.fixture(scope="module")
def deployment():
    try:
        return load_deployment("localhost")
    except FileNotFoundError as error:
        pytest.skip(str(error))


@pytest.fixture
def settler() -> LocalSigner:
    return LocalSigner.from_hex(HARDHAT_ACCOUNTS["deployer"])


@pytest.fixture
def principal() -> LocalSigner:
    return LocalSigner.from_hex(HARDHAT_ACCOUNTS["principal"])


@pytest.fixture
def merchant() -> LocalSigner:
    return LocalSigner.from_hex(HARDHAT_ACCOUNTS["merchant"])


@pytest.fixture
def registry(w3, deployment, settler) -> MandateRegistryClient:
    return MandateRegistryClient(w3, deployment, settler)


@pytest.fixture
def token(w3, deployment):
    return w3.eth.contract(
        address=Web3.to_checksum_address(deployment.settlement_token),
        abi=load_abi("MockXSGD"),
    )


@pytest.fixture
def funded(w3, deployment, token, principal, settler) -> LocalSigner:
    """Mint MockXSGD to the principal and approve the registry to pull it."""
    _send(w3, deployment.chain_id, settler, token.address,
          token.encode_abi(abi_element_identifier="mint", args=[Web3.to_checksum_address(principal.address), FUNDING]))
    _send(w3, deployment.chain_id, principal, token.address,
          token.encode_abi(
              abi_element_identifier="approve",
              args=[Web3.to_checksum_address(deployment.mandate_registry), FUNDING]))
    return principal


def _send(w3: Web3, chain_id: int, signer: LocalSigner, to: str, data: str) -> None:
    transaction = build_transaction(
        w3, sender=signer.address, to=to, data=HexBytes(data), chain_id=chain_id
    )
    tx_hash = send_raw_transaction(w3, sign_transaction(transaction, signer))
    assert w3.eth.wait_for_transaction_receipt(tx_hash)["status"] == 1


def build_worker(registry, deployment):
    queue = InMemorySettlementQueue()
    rail = X402OnchainRail(registry, chain_id=deployment.chain_id)
    audit = InMemoryAuditLog()
    worker = SettlementWorker(
        queue, {RAIL_NAME: rail}, audit, SystemClock(), rail_name=RAIL_NAME
    )
    return queue, audit, worker


def register(registry, request, *, cap: Money | None = None, merchant_address: str | None = None):
    """Record the mandate onchain, optionally with stricter terms than the offchain record."""
    mandate = request.signed_mandate.mandate
    result = registry.register_mandate(
        mandate_id=mandate.mandate_id,
        principal=mandate.principal,
        agent="0x" + "33" * 20,
        merchant=merchant_address or request.charge.payout_address,
        cap=(cap or mandate.max_amount).minor_units,
        expires_at=int(mandate.expires_at.timestamp()),
        mandate_digest=request.signed_mandate.digest,
    )
    assert result.confirmed, f"registration failed: {result.revert}"


def a_request(build, verifier, label, funded, merchant):
    request = settlement_request(
        build,
        verifier,
        **live_overrides(
            build, label, principal=funded.address, payout_address=merchant.address
        ),
    )
    assert request.verdict.decision is Decision.PASS, request.verdict.reason_codes
    return request


class TestHappyPath:
    def test_money_moves_and_the_audit_records_the_transaction(
        self, build, verifier, registry, token, funded, merchant, deployment
    ):
        request = a_request(build, verifier, "live-ok", funded, merchant)
        register(registry, request)

        before = token.functions.balanceOf(Web3.to_checksum_address(merchant.address)).call()
        queue, audit, worker = build_worker(registry, deployment)
        queue.publish(request)

        receipts = worker.process_once()

        assert receipts[0].status is SettlementOutcome.SETTLED, receipts[0].detail
        assert token.functions.balanceOf(Web3.to_checksum_address(merchant.address)).call() == (
            before + request.charge.amount.minor_units
        )
        assert not registry.is_spendable(request.charge.mandate_id)

        entry = audit.all_entries()[0]
        assert entry.settlement.outcome is SettlementOutcome.SETTLED
        assert entry.settlement.reference is not None
        assert queue.pending == [] and queue.dead_letter == []


class TestContractRefuses:
    def test_a_charge_above_the_onchain_cap_is_refused(
        self, build, verifier, registry, funded, merchant, deployment
    ):
        # The Verifier passes: offchain the cap is 5.00 and the charge is 4.20. The contract
        # was registered with a 1.00 cap and refuses independently. This is the case where our
        # own services are wrong and the chain is still right.
        request = a_request(build, verifier, "live-overcap", funded, merchant)
        register(registry, request, cap=Money(currency=Currency.XSGD, amount="1.00"))

        queue, audit, worker = build_worker(registry, deployment)
        queue.publish(request)

        receipts = worker.process_once()

        assert receipts[0].status is SettlementOutcome.REFUSED
        assert receipts[0].reason_code is ReasonCode.CHARGE_OVER_CAP
        assert "AmountExceedsCap" in (receipts[0].detail or "")
        assert registry.is_spendable(request.charge.mandate_id), "a revert must not consume"
        # Terminal, not retried: the contract will not change its mind.
        assert queue.pending == [] and queue.dead_letter == []
        assert audit.all_entries()[0].settlement.outcome is SettlementOutcome.REFUSED

    def test_paying_a_merchant_the_mandate_did_not_bind_is_refused(
        self, build, verifier, registry, funded, merchant, deployment
    ):
        request = a_request(build, verifier, "live-wrongmerchant", funded, merchant)
        register(registry, request, merchant_address="0x" + "99" * 20)

        queue, _, worker = build_worker(registry, deployment)
        queue.publish(request)

        receipts = worker.process_once()

        assert receipts[0].status is SettlementOutcome.REFUSED
        assert receipts[0].reason_code is ReasonCode.MERCHANT_BINDING_MISMATCH
        assert "MerchantMismatch" in (receipts[0].detail or "")


class TestPreflight:
    def test_reports_why_a_spend_would_fail_without_broadcasting(
        self, build, verifier, registry, funded, merchant
    ):
        request = a_request(build, verifier, "live-preflight", funded, merchant)
        register(registry, request, cap=Money(currency=Currency.XSGD, amount="1.00"))

        revert = registry.preflight_spend(
            request.charge.mandate_id,
            request.charge.payout_address,
            request.charge.amount.minor_units,
            request.charge.basket_hash,
        )

        assert revert is not None and revert.error_name == "AmountExceedsCap"
        assert revert.reason_code is ReasonCode.CHARGE_OVER_CAP
        assert registry.is_spendable(request.charge.mandate_id), "preflight must not mutate"


class TestDeploymentGuard:
    def test_deployment_agrees_with_the_wire_contract_on_decimals(self, deployment):
        # CURRENCY_DECIMALS fixes XSGD at 6 and asks track C to confirm it against the
        # deployed token. load_deployment does that; this asserts it was actually checked.
        deployment.assert_decimals_match()
        assert deployment.settlement_token_decimals == 6
