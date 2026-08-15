"""The whole thing, with money actually moving.

`backend/tests/test_rail_end_to_end.py` proves the decisions are right. This
proves the consequences are: that a PASS moves XSGD out of the buyer's wallet,
that a FAIL moves nothing, and that the contract holds its own line even when
everything offchain says yes.

Needs a local node and a deployment:

    cd onchain && npx hardhat node
    cd onchain && npm run deploy:local
    .venv/bin/python -m pytest -m integration

Skips itself otherwise, so the default suite stays offline.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from app.contracts import xsgd
from app.rail import BROWSER_AGENT_ID, build_rail
from web3 import Web3

from tests.settlement_support import HARDHAT_ACCOUNTS, LOCAL_RPC
from trustrail.models.audit import SettlementOutcome
from trustrail.models.verdict import Decision
from trustrail.settlement.chain.deployment import load_deployment
from trustrail.settlement.wiring import build_chain
from trustrail.signing.local import LocalSigner

pytestmark = pytest.mark.integration

FUNDING = 1_000_000_000
INTENT = "toothbrush under $5"
CAP = xsgd("5.00")


@pytest.fixture(scope="module")
def chain():
    """One connection and one deployment for the module.

    Built through `build_chain` rather than by hand, so the wiring the demo
    script and a deployment both use is the wiring under test.
    """
    try:
        load_deployment("localhost")
    except FileNotFoundError as error:
        pytest.skip(str(error))

    wiring = build_chain(
        rpc_url=LOCAL_RPC,
        network="localhost",
        # Roles both default to the deployer on a local deploy. A real
        # deployment separates them; the contract is what enforces that, and
        # `tests/test_settlement_integration.py` covers the refusals.
        registrar_signer=LocalSigner.from_hex(HARDHAT_ACCOUNTS["deployer"]),
        settler_signer=LocalSigner.from_hex(HARDHAT_ACCOUNTS["deployer"]),
    )
    if not wiring.w3.is_connected():
        pytest.skip(f"no chain at {LOCAL_RPC}; run 'npx hardhat node' in onchain/")
    return wiring


@pytest.fixture(scope="module")
def registrar() -> LocalSigner:
    return LocalSigner.from_hex(HARDHAT_ACCOUNTS["deployer"])


@pytest.fixture(scope="module")
def buyer(chain, registrar) -> LocalSigner:
    """A funded principal who has approved the registry to pull from them."""
    principal = LocalSigner.from_hex(HARDHAT_ACCOUNTS["principal"])
    chain.token.mint(registrar, principal.address, FUNDING)
    chain.token.approve(principal, chain.registry_address, FUNDING)
    return principal


@pytest.fixture(scope="module")
def broke() -> LocalSigner:
    """A buyer who has neither tokens nor an allowance."""
    return LocalSigner.generate()


def rail_for(chain, registrar, sku: str):
    return build_rail(preferred_sku=sku, chain=chain, issuer=registrar)


def buy(rail, principal: str):
    return asyncio.run(
        rail.orchestrator.purchase(
            principal=principal,
            agent_id=BROWSER_AGENT_ID,
            intent=INTENT,
            max_amount=CAP,
            ttl=timedelta(minutes=10),
        )
    )


def merchant_balance(chain, rail) -> int:
    [merchant] = rail.merchants.list_all()
    return chain.token.balance_of(merchant.payout_address)


class TestMoneyMoves:
    def test_a_clean_purchase_transfers_xsgd_to_the_merchant(
        self, chain, registrar, buyer
    ):
        rail = rail_for(chain, registrar, "TB-SOFT-2PK")
        before = merchant_balance(chain, rail)

        outcome = buy(rail, buyer.address)
        [receipt] = rail.settle_pending()

        assert outcome.decision is Decision.PASS
        assert receipt.status is SettlementOutcome.SETTLED
        assert receipt.reference  # a transaction hash
        assert merchant_balance(chain, rail) - before == 4_200_000

    def test_the_buyer_keeps_custody_until_the_moment_it_settles(
        self, chain, registrar, buyer
    ):
        """Funds leave the buyer's own wallet. The contract never holds them."""
        rail = rail_for(chain, registrar, "TB-SOFT-2PK")
        buyer_before = chain.token.balance_of(buyer.address)
        registry_before = chain.token.balance_of(chain.registry_address)

        buy(rail, buyer.address)
        rail.settle_pending()

        assert buyer_before - chain.token.balance_of(buyer.address) == 4_200_000
        assert chain.token.balance_of(chain.registry_address) == registry_before == 0


class TestTheLedgerIsTheRecord:
    def test_the_mandate_is_on_chain_before_anything_is_bought(
        self, chain, registrar, buyer
    ):
        rail = rail_for(chain, registrar, "TB-SOFT-2PK")

        outcome = buy(rail, buyer.address)
        onchain = chain.registry.get_mandate(outcome.mandate.mandate_id)

        assert onchain["exists"]
        assert onchain["principal"].lower() == buyer.address.lower()
        assert onchain["cap"] == CAP.minor_units
        # Minted before a product was chosen, so no merchant is bound yet.
        assert int(onchain["merchant"], 16) == 0

    def test_settling_consumes_the_mandate_on_chain(self, chain, registrar, buyer):
        """One-time consumption is the contract's, not just ours."""
        rail = rail_for(chain, registrar, "TB-SOFT-2PK")
        outcome = buy(rail, buyer.address)
        rail.settle_pending()

        onchain = chain.registry.get_mandate(outcome.mandate.mandate_id)

        assert onchain["consumed"]
        assert not chain.registry.is_spendable(outcome.mandate.mandate_id)


class TestRejectionsNeverReachTheChain:
    def test_an_injected_listing_moves_no_money(self, chain, registrar, buyer):
        rail = rail_for(chain, registrar, "TB-INJECTION")
        before = merchant_balance(chain, rail)

        outcome = buy(rail, buyer.address)
        receipts = rail.settle_pending()

        assert outcome.decision is Decision.FAIL
        assert receipts == []
        assert merchant_balance(chain, rail) == before

    def test_a_rejected_mandate_is_left_unconsumed_on_chain(
        self, chain, registrar, buyer
    ):
        """It was registered at mint, and it simply expires unused."""
        rail = rail_for(chain, registrar, "TB-INJECTION")
        outcome = buy(rail, buyer.address)
        rail.settle_pending()

        onchain = chain.registry.get_mandate(outcome.mandate.mandate_id)

        assert onchain["exists"]
        assert not onchain["consumed"]


class TestHumanApproval:
    def test_an_approved_review_settles_for_the_amount_approved(
        self, chain, registrar, buyer
    ):
        rail = rail_for(chain, registrar, "TB-SUSPICIOUS")
        before = merchant_balance(chain, rail)
        outcome = buy(rail, buyer.address)
        assert outcome.decision is Decision.REVIEW
        assert rail.settle_pending() == []  # nothing moves while it is held

        rail.orchestrator.approve_review(
            outcome.charge.charge_id, approved_by="ernest"
        )
        [receipt] = rail.settle_pending()

        assert receipt.status is SettlementOutcome.SETTLED
        assert merchant_balance(chain, rail) - before == 500_000

    def test_a_killed_review_moves_nothing_and_revokes_on_chain(
        self, chain, registrar, buyer
    ):
        rail = rail_for(chain, registrar, "TB-SUSPICIOUS")
        before = merchant_balance(chain, rail)
        outcome = buy(rail, buyer.address)

        rail.orchestrator.kill_review(outcome.charge.charge_id, killed_by="ernest")

        assert rail.settle_pending() == []
        assert merchant_balance(chain, rail) == before
        onchain = chain.registry.get_mandate(outcome.mandate.mandate_id)
        assert onchain["revoked"]


class TestFundingPreflight:
    def test_an_unfunded_buyer_gets_a_readable_refusal(self, chain, registrar, broke):
        """Not an opaque ERC-20 revert, and not a retryable error either."""
        rail = rail_for(chain, registrar, "TB-SOFT-2PK")

        buy(rail, broke.address)
        [receipt] = rail.settle_pending()

        assert receipt.status is SettlementOutcome.REFUSED
        assert "top up" in receipt.detail
        assert not receipt.retryable

    def test_a_funded_buyer_who_never_approved_is_told_so(self, chain, registrar):
        holder = LocalSigner.generate()
        chain.token.mint(registrar, holder.address, FUNDING)
        rail = rail_for(chain, registrar, "TB-SOFT-2PK")

        buy(rail, holder.address)
        [receipt] = rail.settle_pending()

        assert receipt.status is SettlementOutcome.REFUSED
        assert "approve()" in receipt.detail


def test_the_deployed_token_really_has_six_decimals(chain):
    """CLAUDE.md asks track C to confirm this against the live token.

    `load_deployment` already asserts it, so reaching here means it held; this
    states the number so a change is visible in a diff rather than only in a
    raised exception.
    """
    assert chain.token.decimals() == 6
    assert chain.deployment.settlement_token_decimals == 6
    assert Web3.is_address(chain.registry_address)
