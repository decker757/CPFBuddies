"""Force a charge past the Verifier and let the contract refuse it.

This is CLAUDE.md demo step 7, the moment the whole pitch rests on: *even if
every offchain check is bypassed, the money still does not move.* The Verifier
catches an over-cap charge long before settlement, which is why this script has
to go around it deliberately -- it mints a mandate, then calls
`MandateRegistry.spend` directly with an amount the cap forbids.

    python scripts/demo_revert.py --network avalanche

What it produces is a **reverted transaction on a public explorer**. That is the
artifact: a hash anyone can open, showing our own settler key trying to overspend
a mandate and the contract refusing.

**It cannot be rehearsed against a local node.** Hardhat simulates first and
refuses to broadcast a doomed transaction, so there is no hash to show. Public
RPC mines it and it reverts with a receipt. Fuji or mainnet only.

Gas is spent and nothing else: a reverted transfer moves no tokens, so this costs
a fraction of a cent and no XSGD.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

from app.contracts import xsgd
from app.rail import BROWSER_AGENT_ID, build_rail, load_environment

from trustrail.settlement.chain.explorer import transaction_url
from trustrail.settlement.wiring import build_chain, check_deployment
from trustrail.signing.local import LocalSigner
from trustrail.verifier.config import VerifierConfig

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "verifier.toml"

#: Any 32 bytes. The contract binds the basket on first spend; for a charge that
#: never lands, what it binds to is immaterial.
BASKET_HASH = "0x" + "de" * 32


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    load_environment()

    registrar = _require("TRUSTRAIL_REGISTRAR_KEY")
    settler = _require("TRUSTRAIL_SETTLER_KEY")
    buyer = _require("TRUSTRAIL_PRINCIPAL_KEY", fallback="DEPLOYER_PRIVATE_KEY")

    chain = build_chain(
        rpc_url=args.rpc,
        network=args.network,
        registrar_signer=registrar,
        settler_signer=settler,
    )
    if not chain.w3.is_connected():
        return _fail(f"no chain at {args.rpc}")

    domain = VerifierConfig.from_toml(CONFIG_PATH).domain
    problems = check_deployment(
        chain.deployment,
        registrar_address=registrar.address,
        settler_address=settler.address,
        domain=domain,
    )
    if problems:
        return _fail("; ".join(problems))

    if args.network in {"localhost", "hardhat"}:
        return _fail(
            "a local node simulates first and refuses to broadcast a doomed "
            "transaction, so there is no hash to show. Use --network avalanche."
        )

    rail = build_rail(chain=chain, issuer=registrar, domain=domain)
    [merchant] = rail.merchants.list_all()

    print(f"network   {chain.deployment.network} (chain {chain.deployment.chain_id})")
    print(f"registry  {chain.registry_address}")
    print(f"buyer     {buyer.address}")
    print(f"merchant  {merchant.payout_address}")
    print(f"mandate cap {args.cap} XSGD, forcing a spend of {args.amount} XSGD\n")

    record = rail.mandates.mint(
        principal=buyer.address,
        agent_id=BROWSER_AGENT_ID,
        max_amount=xsgd(args.cap),
        intent="toothbrush under $5",
        ttl=timedelta(minutes=10),
        agent_address=buyer.address,
    )
    print(f"minted    {record.mandate_id}")
    print("          registered onchain, cap enforced in Solidity\n")

    # Straight at the contract. No Verifier, no verdict, no queue -- this is the
    # compromised-backend case, and the point is that it changes nothing.
    amount = xsgd(args.amount).minor_units
    print(f"forcing   spend({record.mandate_id[:14]}…, {merchant.payout_address[:12]}…, {amount})")
    result = chain.registry.spend(
        record.mandate_id, merchant.payout_address, amount, BASKET_HASH
    )

    print(f"\nstatus    {result.status}")
    if result.revert is not None:
        print(f"reason    {result.revert.error_name or 'undecodable'}")
    if result.tx_hash:
        url = transaction_url(chain.deployment.chain_id, result.tx_hash)
        print(f"tx        {result.tx_hash}")
        print(f"explorer  {url}")
    else:
        print(
            "no hash: this node refused to broadcast rather than mining the "
            "revert. Nothing was overspent, but there is no artifact either."
        )

    if result.status != "REVERTED":
        return _fail(
            f"expected the contract to refuse, got {result.status}. "
            "Check the cap and the amount."
        )

    print("\nThe contract refused. No XSGD moved, and the refusal is public.")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="avalanche")
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--cap", default="5.00", help="the mandate's cap")
    parser.add_argument("--amount", default="500.00", help="what to try to spend")
    args = parser.parse_args()
    if args.rpc is None:
        import os

        args.rpc = os.environ.get(
            "TRUSTRAIL_RPC_URL", "https://api.avax.network/ext/bc/C/rpc"
        )
    return args


def _require(env_var: str, *, fallback: str | None = None) -> LocalSigner:
    import os

    key = os.environ.get(env_var) or (os.environ.get(fallback) if fallback else None)
    if not key:
        raise SystemExit(f"error: {env_var} is not set")
    return LocalSigner.from_hex(key)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
