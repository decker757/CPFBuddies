"""Run one purchase intent all the way to a transaction on chain.

This is the end-to-end demo: stub marketplace, Browser Agent, Evaluator,
Verifier, settlement queue, and `MandateRegistry.spend` moving XSGD from the
buyer's wallet to the merchant's.

    # terminal 1
    cd onchain && npx hardhat node
    # terminal 2
    cd onchain && npm run deploy:local
    .venv/bin/python scripts/demo_onchain.py --fund

Then walk the demo:

    --sku TB-SOFT-2PK      clean purchase, PASS, money moves
    --sku TB-INJECTION     prompt injection, FAIL, nothing moves
    --sku GIFT-SUBSTITUTE  substitution, FAIL, nothing moves
    --sku TB-SUSPICIOUS    REVIEW, held for a human; add --approve to release it

On Fuji, pass `--network fuji --rpc https://api.avax-test.network/ext/bc/C/rpc`
and set the three keys from the environment. Note that a doomed transaction
cannot be shown reverting against a local node: Hardhat simulates first and
refuses to broadcast, so there is no hash. Public RPC mines it and it reverts
with a receipt, which is the version worth demoing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from app.contracts import xsgd
from app.rail import BROWSER_AGENT_ID, build_rail

from trustrail.models.verdict import Decision
from trustrail.settlement.wiring import build_chain
from trustrail.signing.local import LocalSigner
from trustrail.verifier.config import VerifierConfig

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "verifier.toml"

# Hardhat's deterministic development accounts. Public knowledge, worthless,
# and local only -- never put a key with value in a source file.
HARDHAT = {
    "deployer": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "principal": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
}
LOCAL_RPC = "http://127.0.0.1:8545"
FUNDING = 1_000_000_000  # minor units minted to the buyer with --fund


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    registrar = _signer("TRUSTRAIL_REGISTRAR_KEY", HARDHAT["deployer"])
    settler = _signer("TRUSTRAIL_SETTLER_KEY", HARDHAT["deployer"])
    buyer = _signer("TRUSTRAIL_PRINCIPAL_KEY", HARDHAT["principal"])

    chain = build_chain(
        rpc_url=args.rpc,
        network=args.network,
        registrar_signer=registrar,
        settler_signer=settler,
    )
    if not chain.w3.is_connected():
        return _fail(f"no chain at {args.rpc}. Is 'npx hardhat node' running?")

    problem = _check_roles(chain, registrar, settler)
    if problem:
        return _fail(problem)

    # The domain comes from the committed config, not from a default. Without
    # this the Mandate Service and Verifier agree with each other but sign
    # under a domain no deployed contract shares, and the cutover in
    # `config/verifier.toml` silently does nothing.
    domain = VerifierConfig.from_toml(CONFIG_PATH).domain
    problem = _check_domain(chain, domain)
    if problem:
        return _fail(problem)

    _banner(chain, args, buyer, domain)

    if args.fund:
        _fund(chain, registrar, buyer)

    rail = build_rail(
        preferred_sku=args.sku, chain=chain, issuer=registrar, domain=domain
    )
    [merchant] = rail.merchants.list_all()
    before = chain.token.balance_of(merchant.payout_address)

    outcome = asyncio.run(
        rail.orchestrator.purchase(
            principal=buyer.address,
            agent_id=BROWSER_AGENT_ID,
            intent=args.intent,
            max_amount=xsgd(args.cap),
            ttl=timedelta(minutes=10),
        )
    )
    _report_verdict(outcome)

    if outcome.decision is Decision.REVIEW and args.approve:
        print("\n-- human approves the held charge --")
        outcome = rail.orchestrator.approve_review(
            outcome.charge.charge_id, approved_by="demo-operator"
        )
        _report_verdict(outcome)

    receipts = rail.settle_pending()
    _report_settlement(chain, receipts, merchant.payout_address, before)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="localhost")
    parser.add_argument("--rpc", default=os.environ.get("TRUSTRAIL_RPC_URL", LOCAL_RPC))
    parser.add_argument("--sku", default="TB-SOFT-2PK")
    parser.add_argument("--intent", default="toothbrush under $5")
    parser.add_argument("--cap", default="5.00")
    parser.add_argument(
        "--fund",
        action="store_true",
        help="mint MockXSGD to the buyer and approve the registry. Testnet only.",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="approve a REVIEW as if a human had, so the demo continues to settlement",
    )
    return parser.parse_args()


def _signer(env_var: str, fallback: str) -> LocalSigner:
    return LocalSigner.from_hex(os.environ.get(env_var, fallback))


def _check_roles(chain, registrar: LocalSigner, settler: LocalSigner) -> str | None:
    """Refuse to start if the keys do not hold the roles the deployment granted.

    Getting this wrong produces an `AccessControlUnauthorizedAccount` revert
    several steps later, at mint or at settlement, which is a slow way to learn
    that the deploy script granted the roles to somebody else.
    """
    expected_registrar = chain.deployment.registrar.lower()
    expected_settler = chain.deployment.settler.lower()
    if registrar.address.lower() != expected_registrar:
        return (
            f"registrar key is {registrar.address} but {chain.deployment.network} "
            f"granted REGISTRAR_ROLE to {expected_registrar}. "
            f"Set TRUSTRAIL_REGISTRAR_KEY."
        )
    if settler.address.lower() != expected_settler:
        return (
            f"settler key is {settler.address} but {chain.deployment.network} "
            f"granted SETTLER_ROLE to {expected_settler}. Set TRUSTRAIL_SETTLER_KEY."
        )
    return None


def _check_domain(chain, domain) -> str | None:
    """Refuse to run if the signing domain does not name the deployed registry.

    A mismatch here is invisible at runtime — mandates still verify, because the
    Mandate Service and the Verifier read the same config — but the digest
    recorded onchain is computed under a domain no contract shares. Better to
    stop than to produce an audit trail that quietly means nothing.
    """
    if domain.chain_id != chain.deployment.chain_id:
        return (
            f"config/verifier.toml signs for chain {domain.chain_id} but the "
            f"deployment is chain {chain.deployment.chain_id}"
        )
    if domain.verifying_contract.lower() != chain.deployment.mandate_registry.lower():
        return (
            f"config/verifier.toml names {domain.verifying_contract} as the "
            f"verifying contract, but the deployed registry is "
            f"{chain.deployment.mandate_registry}"
        )
    return None


def _banner(chain, args, buyer: LocalSigner, domain) -> None:
    print(f"network   {chain.deployment.network} (chain {chain.deployment.chain_id})")
    print(f"registry  {chain.registry_address}")
    print(f"domain    {domain.name} v{domain.version} @ {domain.verifying_contract}")
    print(
        f"token     {chain.deployment.settlement_token} "
        f"({chain.deployment.settlement_token_symbol}, "
        f"{chain.deployment.settlement_token_decimals} decimals)"
    )
    print(f"buyer     {buyer.address}")
    print(f"intent    {args.intent!r} up to {args.cap} XSGD, sku {args.sku}")
    print()


def _fund(chain, minter: LocalSigner, buyer: LocalSigner) -> None:
    """Give the buyer tokens and let the registry pull them.

    The approval is the buyer's own transaction and the one moment their key
    has to act. It is a one-time setup, not a per-purchase step: from here the
    mandate is what bounds the spend, not the allowance.
    """
    print("funding the buyer...")
    try:
        chain.token.mint(minter, buyer.address, FUNDING)
    except Exception as error:  # noqa: BLE001 - real XSGD has no open mint
        print(f"  mint failed ({type(error).__name__}); assuming the buyer is funded")
    chain.token.approve(buyer, chain.registry_address, FUNDING)
    print(
        f"  balance   {chain.token.balance_of(buyer.address)}\n"
        f"  allowance {chain.token.allowance(buyer.address, chain.registry_address)}\n"
    )


def _report_verdict(outcome) -> None:
    print(f"verdict   {outcome.decision}")
    print(f"  risk score  {outcome.verdict.risk_score}")
    print(f"  reasons     {[str(c) for c in outcome.verdict.reason_codes] or '-'}")
    print(f"  charge      {outcome.charge.sku} at {outcome.charge.amount}")
    if outcome.verdict.failed_deterministically:
        print("  this was a FACT, not a threshold: no human may override it")
    if outcome.hold is not None:
        print(f"  held until  {outcome.hold.deadline.isoformat()}")
    print(f"  queued      {outcome.settling}")
    print()


def _report_settlement(chain, receipts, merchant: str, before: int) -> None:
    if not receipts:
        print("settlement  nothing queued; no transaction was attempted")
        return

    for receipt in receipts:
        print(f"settlement  {receipt.status} on {receipt.rail}")
        if receipt.reference:
            print(f"  tx        {receipt.reference}")
        if receipt.explorer_url:
            print(f"  explorer  {receipt.explorer_url}")
        if receipt.reason_code:
            print(f"  reason    {receipt.reason_code}")
        if receipt.detail:
            print(f"  detail    {receipt.detail}")

    after = chain.token.balance_of(merchant)
    print(f"\nmerchant {merchant}")
    print(f"  before {before}\n  after  {after}\n  moved  {after - before} minor units")


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
