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
from app.rail import BROWSER_AGENT_ID, build_rail, load_environment

from trustrail.models.verdict import Decision
from trustrail.settlement.wiring import build_chain, check_deployment
from trustrail.signing.local import LocalSigner
from trustrail.verifier.config import VerifierConfig

logger = logging.getLogger(__name__)

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
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    # The same env files the server reads. Without this the script sees only
    # what the shell exported, so a populated `.env` and a run against Hardhat's
    # public dev keys look identical until a mainnet transaction fails.
    load_environment()

    registrar = _signer("TRUSTRAIL_REGISTRAR_KEY", HARDHAT["deployer"])
    settler = _signer("TRUSTRAIL_SETTLER_KEY", HARDHAT["deployer"])
    buyer = _buyer_signer()

    chain = build_chain(
        rpc_url=args.rpc,
        network=args.network,
        registrar_signer=registrar,
        settler_signer=settler,
    )
    if not chain.w3.is_connected():
        return _fail(f"no chain at {args.rpc}. Is 'npx hardhat node' running?")

    # The domain comes from the committed config, not from a default. Without
    # this the Mandate Service and Verifier agree with each other but sign
    # under a domain no deployed contract shares, and the cutover in
    # `config/verifier.toml` silently does nothing.
    domain = VerifierConfig.from_toml(CONFIG_PATH).domain
    problems = check_deployment(
        chain.deployment,
        registrar_address=registrar.address,
        settler_address=settler.address,
        domain=domain,
    )
    if problems:
        return _fail("; ".join(problems))

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


def _buyer_signer() -> LocalSigner:
    """The wallet the XSGD is spent from.

    Falls back to `DEPLOYER_PRIVATE_KEY` because on this demo deployment the
    person who deployed the contract is also the person holding the XSGD, and
    copying one private key into a second file to say so would mean two places
    to rotate it and two chances to leak it.

    **The fallback is loud, and it belongs to this script alone.** `chain_app`
    never reads a buyer key at all -- it does not need one, since `spend()` is
    the settler's transaction and pulls through an allowance. A deployment where
    the buyer is the deployer is a demo, not a product: a real principal is a
    customer, and nothing about that wallet should be reachable from ours.
    """
    explicit = os.environ.get("TRUSTRAIL_PRINCIPAL_KEY")
    if explicit:
        return LocalSigner.from_hex(explicit)

    deployer = os.environ.get("DEPLOYER_PRIVATE_KEY")
    if deployer:
        signer = LocalSigner.from_hex(deployer)
        logger.warning(
            "TRUSTRAIL_PRINCIPAL_KEY is unset; buying as the deployer %s. "
            "Fine for a demo, never for a deployment.",
            signer.address,
        )
        return signer

    return LocalSigner.from_hex(HARDHAT["principal"])


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
