"""Make the contract refuse a spend, on a public chain, and keep the receipt.

This is demo step 7, the moment the whole pitch rests on: not "our software
rejected the charge" -- the Verifier already does that, and a judge has only our
word for it -- but "the agent got past every check we wrote and the chain still
refused."

So this script deliberately bypasses the Verifier. It mints a real mandate
through the Mandate Service, which registers it onchain with its cap, and then
calls `MandateRegistry.spend` directly with an amount above that cap, signing as
the settler. That is exactly what a fully compromised Settlement Worker could
do, and it is the strongest version of the claim: the only thing standing
between it and the buyer's XSGD is Solidity.

    .venv/bin/python scripts/demo_revert.py --network avalanche

What lands on the explorer is a transaction with status 0 and
`AmountExceedsCap(mandateId, cap, requested)` in it. Nothing else moves.

**This cannot be rehearsed against a local node.** Hardhat simulates first and
refuses to broadcast a doomed transaction, so there is no hash and nothing to
show. A public RPC mines it and produces a receipt, which is the version worth
demoing -- so this script refuses to run anywhere without a block explorer.

Cost is two transactions of gas and no XSGD at all: the cap check runs before
the transfer, so the buyer's balance is never touched.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from app.contracts import xsgd
from app.rail import BROWSER_AGENT_ID, build_rail, load_environment

from trustrail.models.primitives import new_hex32
from trustrail.settlement.chain.explorer import transaction_url
from trustrail.settlement.wiring import build_chain, check_deployment
from trustrail.signing.local import LocalSigner
from trustrail.verifier.config import VerifierConfig

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "verifier.toml"

PUBLIC_AVALANCHE_RPC = "https://api.avax.network/ext/bc/C/rpc"

#: The intent the mandate is minted under. Wording matters on screen: the cap a
#: judge sees onchain should obviously belong to the toothbrush story, so that
#: the amount we then try to push through is obviously not.
INTENT = "toothbrush under $5"


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    load_environment()

    registrar = _required_signer("TRUSTRAIL_REGISTRAR_KEY")
    settler = _required_signer("TRUSTRAIL_SETTLER_KEY")
    if registrar is None or settler is None:
        return _fail(
            "TRUSTRAIL_REGISTRAR_KEY and TRUSTRAIL_SETTLER_KEY must both be set. "
            "This script signs as the settler on purpose -- there is no local "
            "fallback, because a local node cannot show a revert."
        )

    principal = args.principal or _principal_from_environment()
    if principal is None:
        return _fail(
            "no principal. Pass --principal 0x..., or set DEPLOYER_PRIVATE_KEY "
            "so the buyer's address can be derived from it."
        )

    rpc_url = args.rpc or os.environ.get("TRUSTRAIL_RPC_URL", PUBLIC_AVALANCHE_RPC)
    chain = build_chain(
        rpc_url=rpc_url,
        network=args.network,
        registrar_signer=registrar,
        settler_signer=settler,
    )
    if not chain.w3.is_connected():
        return _fail(f"no chain at {rpc_url}")

    # No explorer means no artifact, and it almost certainly means a local node,
    # which will refuse to broadcast this at all. Fail here with the reason
    # rather than three steps later with a transport error.
    if transaction_url(chain.deployment.chain_id, "0x" + "00" * 32) is None:
        return _fail(
            f"chain {chain.deployment.chain_id} has no block explorer, so there "
            f"would be nothing to show. A local node also simulates first and "
            f"refuses to broadcast a doomed transaction. Use avalanche or fuji."
        )

    domain = VerifierConfig.from_toml(CONFIG_PATH).domain
    problems = check_deployment(
        chain.deployment,
        registrar_address=registrar.address,
        settler_address=settler.address,
        domain=domain,
    )
    if problems:
        return _fail("; ".join(problems))

    rail = build_rail(chain=chain, issuer=registrar, domain=domain)
    [merchant] = rail.merchants.list_all()

    cap = xsgd(args.cap)
    attempt = xsgd(args.attempt)
    if attempt.minor_units <= cap.minor_units:
        return _fail(
            f"--attempt must exceed --cap, or the contract has nothing to refuse "
            f"({attempt} is not more than {cap})"
        )

    _banner(chain, merchant, principal, cap, attempt)

    # 1. A real mandate, minted the way every other mandate is minted. Using the
    #    Mandate Service rather than calling registerMandate here is the point:
    #    what gets refused in step 3 is not a special case built for the demo.
    print("minting the mandate...")
    record = rail.mandates.mint(
        principal=principal,
        agent_id=BROWSER_AGENT_ID,
        max_amount=cap,
        intent=INTENT,
        ttl=timedelta(minutes=args.ttl),
    )
    mandate_id = record.mandate_id
    onchain = _await_registration(chain, mandate_id)
    if onchain is None:
        return _fail(
            "the registration confirmed but the node still will not serve the "
            "mandate. Re-run: the mandate is registered, so this costs another "
            "mint but loses nothing."
        )
    print(f"  mandate   {mandate_id}")
    print(f"  cap       {onchain['cap']} minor units, public and checkable")
    print(f"  expires   {onchain['expiresAt']}")
    print(f"  spendable {chain.registry.is_spendable(mandate_id)}\n")

    basket_hash = new_hex32()
    amount = attempt.minor_units

    # 2. Ask the contract what it would do. Free, and it means the broadcast
    #    below is a deliberate act rather than a hopeful one.
    would = chain.registry.preflight_spend(
        mandate_id, merchant.payout_address, amount, basket_hash
    )
    print(f"preflight   the contract would refuse: {would}")
    print("            a compromised worker would not have asked. Broadcasting.\n")

    if not args.yes and not _confirm(chain, attempt):
        return _fail("aborted before broadcasting; nothing was sent")

    # 3. The bypass. Straight at the contract with the settler's key, past the
    #    Verifier, past the orchestrator, past the rail's own preflight.
    print("broadcasting the over-cap spend...")
    before = chain.token.balance_of(merchant.payout_address)
    result = chain.registry.spend(
        mandate_id, merchant.payout_address, amount, basket_hash
    )

    if result.confirmed:
        # Alarming enough to be the loudest thing this script can say. It would
        # mean the deployed contract does not enforce the cap it recorded.
        return _fail(
            f"THE SPEND SUCCEEDED. tx {result.tx_hash}. The contract did not "
            f"enforce the cap -- stop and investigate before demoing anything."
        )

    _report(chain, result, merchant.payout_address, before, mandate_id)
    return 0


def _await_registration(
    chain, mandate_id: str, attempts: int = 10, delay: float = 1.5
) -> dict | None:
    """Read the mandate back, tolerating a node that has not caught up yet.

    The public RPC serves stale reads immediately after a transaction whose
    receipt already confirmed it -- CLAUDE.md records this as found the hard
    way. Reading once would print a zero cap and send the preflight below to
    `MandateNotFound`, which demonstrates something entirely different from the
    cap being enforced. `getMandate` reverts on an unknown id, so an exception
    here is the same answer as a miss.
    """
    for _ in range(attempts):
        try:
            record = chain.registry.get_mandate(mandate_id)
        except Exception:  # noqa: BLE001 - a stale node and an unknown id look alike
            record = None
        if record is not None and record["exists"]:
            return record
        time.sleep(delay)
    return None


def _report(chain, result, merchant: str, before: int, mandate_id: str) -> None:
    url = transaction_url(chain.deployment.chain_id, result.tx_hash or "")
    print(f"reverted    {result.revert}")
    if result.revert is not None and result.revert.reason_code is not None:
        print(f"  reason    {result.revert.reason_code}")
    print(f"  tx        {result.tx_hash}")
    print(f"  block     {result.block_number}")
    print(f"  gas used  {result.gas_used}")
    if url:
        print(f"  explorer  {url}")

    # What a revert means, stated as two readings rather than asserted. Both are
    # cheap and both are the answer to "how do we know nothing happened".
    after = chain.token.balance_of(merchant)
    print("\nnothing moved")
    print(f"  merchant balance  {before} -> {after}")
    print(f"  mandate spendable {chain.registry.is_spendable(mandate_id)} (unconsumed)")
    print(
        "\nThe mandate is still live and still capped. A revert is not a state "
        "change;\nit is the absence of one."
    )


def _confirm(chain, attempt) -> bool:
    print(
        f"About to send a transaction on {chain.deployment.network} "
        f"(chain {chain.deployment.chain_id}) that is expected to revert.\n"
        f"It costs gas and moves no {attempt.currency.value}."
    )
    return input("Type 'revert' to broadcast: ").strip().lower() == "revert"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="avalanche")
    # Resolved after `load_environment`, not here: an argparse default is
    # evaluated at import time, which is before the `.env` files are read, so a
    # default taken from the environment would silently ignore them.
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--cap", default="5.00", help="the mandate's cap, in XSGD")
    parser.add_argument(
        "--attempt",
        default="50.00",
        help="what the compromised worker tries to spend. Must exceed --cap.",
    )
    parser.add_argument(
        "--principal",
        default=None,
        help="the buyer whose wallet the mandate is written against",
    )
    parser.add_argument(
        "--ttl", type=int, default=10, help="mandate lifetime in minutes"
    )
    parser.add_argument(
        "--yes", action="store_true", help="broadcast without the confirmation prompt"
    )
    return parser.parse_args()


def _required_signer(env_var: str) -> LocalSigner | None:
    key = os.environ.get(env_var)
    return LocalSigner.from_hex(key) if key else None


def _principal_from_environment() -> str | None:
    """The buyer's address, derived from whichever key names them.

    Only the address is used -- this script never signs as the buyer, because
    nothing here spends their tokens. The cap check reverts before the transfer.
    """
    for env_var in ("TRUSTRAIL_PRINCIPAL_KEY", "DEPLOYER_PRIVATE_KEY"):
        key = os.environ.get(env_var)
        if key:
            return LocalSigner.from_hex(key).address
    return None


def _banner(chain, merchant, principal: str, cap, attempt) -> None:
    print(f"network   {chain.deployment.network} (chain {chain.deployment.chain_id})")
    print(f"registry  {chain.registry_address}")
    print(f"principal {principal}")
    print(f"merchant  {merchant.payout_address}")
    print(f"story     mandate capped at {cap}; the worker will try {attempt}")
    print()


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
