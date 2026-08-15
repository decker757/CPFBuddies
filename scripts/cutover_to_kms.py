"""Move REGISTRAR_ROLE and SETTLER_ROLE onto KMS-held keys.

CLAUDE.md wants no private key material outside KMS. The keys exist and sign
correctly; what is left is that the contract has never heard of them. This
grants each KMS address its role, funds it with gas, and rewrites
`onchain/deployments/avalanche.json` so `check_deployment` stops refusing to
start.

    .venv/bin/python scripts/cutover_to_kms.py --dry-run
    .venv/bin/python scripts/cutover_to_kms.py

**The old EOA roles are left in place.** Granting is additive, so after this
runs there are two holders of each role and the demo keeps working on whichever
you point it at. Revoking is a separate, later decision — `--revoke-old` does it
— because a cutover that removes the working path before the new one has settled
anything is how a demo dies. Note the security claim is unchanged either way:
REGISTRAR and SETTLER are still held by different keys, which is what makes "a
compromised settler cannot exceed the cap" true.

The admin wallet signs all of this. It holds `DEFAULT_ADMIN_ROLE` and neither
role, so it can hand out authority it cannot itself use — which is the only
reason this script is possible without touching the existing role keys.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from app.rail import load_environment
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3

from trustrail.settlement.chain.deployment import load_abi, load_deployment
from trustrail.settlement.chain.explorer import transaction_url
from trustrail.settlement.chain.transactions import (
    MIN_PRIORITY_FEE_WEI,
    build_transaction,
    send_raw_transaction,
    sign_transaction,
)
from trustrail.signing.kms import KmsSigner
from trustrail.signing.local import LocalSigner

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_FILE = REPO_ROOT / "onchain" / "deployments" / "avalanche.json"

REGISTRAR_ALIAS = "alias/trustrail-registrar"
SETTLER_ALIAS = "alias/trustrail-settler"

REGISTRAR_ROLE = keccak(text="REGISTRAR_ROLE")
SETTLER_ROLE = keccak(text="SETTLER_ROLE")
DEFAULT_ADMIN_ROLE = b"\x00" * 32

#: Gas money per key, in AVAX. A registration costs ~0.00018 and a spend
#: ~0.00012, so this is roughly a hundred mandates -- the same float the
#: existing role keys were given.
FUNDING_AVAX = 0.02
TRANSFER_GAS = 21_000


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")
    load_environment()

    admin_key = os.environ.get("DEPLOYER_PRIVATE_KEY")
    if not admin_key:
        return _fail("DEPLOYER_PRIVATE_KEY is unset; it holds DEFAULT_ADMIN_ROLE")
    admin = LocalSigner.from_hex(admin_key)

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        return _fail(f"no chain at {args.rpc}")
    deployment = load_deployment(args.network)
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(deployment.mandate_registry),
        abi=load_abi("MandateRegistry"),
    )

    # The Signer port returns lowercase; web3 refuses anything but a checksum
    # address. Converting at the boundary, as `MandateRegistryClient` does.
    admin_address = Web3.to_checksum_address(admin.address)
    if not registry.functions.hasRole(DEFAULT_ADMIN_ROLE, admin_address).call():
        return _fail(
            f"{admin.address} does not hold DEFAULT_ADMIN_ROLE on "
            f"{deployment.mandate_registry}; it cannot grant anything"
        )

    targets = [
        ("REGISTRAR_ROLE", REGISTRAR_ROLE, KmsSigner(REGISTRAR_ALIAS).address),
        ("SETTLER_ROLE", SETTLER_ROLE, KmsSigner(SETTLER_ALIAS).address),
    ]

    _banner(deployment, admin, targets, w3, dry_run=args.dry_run)
    if args.dry_run:
        print("\nnothing was sent. Re-run without --dry-run to apply.")
        return 0

    for name, role, address in targets:
        _fund(w3, admin, address, deployment.chain_id, args.fund)
        _grant(w3, registry, admin, name, role, address, deployment.chain_id)

    print("\nverifying against chain state")
    for name, role, address in targets:
        held = _confirm_role(registry, role, address)
        print(f"  {name:<15} {address}  {'HELD' if held else 'NOT HELD'}")
        if not held:
            return _fail(f"{address} still does not hold {name}; stopping")

    if args.revoke_old:
        _revoke_old(w3, registry, admin, deployment)

    _rewrite_deployment(targets)
    print(
        "\nDone. `chain_app` will now accept the KMS signers. Point it at them with\n"
        "TRUSTRAIL_REGISTRAR_KEY_KMS / TRUSTRAIL_SETTLER_KEY_KMS and drop the\n"
        "private keys from .env."
    )
    return 0


def _confirm_role(registry: Any, role: bytes, address: str, attempts: int = 8) -> bool:
    """Re-read until the node admits what the receipt already proved.

    The public RPC serves stale reads immediately after a transaction — the same
    behaviour CLAUDE.md records for an allowance read after `approve()`. A single
    `hasRole` here reported NOT HELD for a grant whose receipt was status 1 with
    a `RoleGranted` log in it, which would have aborted a cutover that had in
    fact already succeeded. Trust the receipt, and give the node a moment to
    agree.
    """
    target = Web3.to_checksum_address(address)
    for attempt in range(attempts):
        if registry.functions.hasRole(role, target).call():
            return True
        if attempt < attempts - 1:
            time.sleep(2)
    return False


def _fund(w3: Web3, admin: LocalSigner, address: str, chain_id: int, amount: float) -> None:
    """Send gas money, unless the address already has some.

    Skipped rather than topped up: re-running this script should not keep
    draining the admin wallet into an address that is already funded.
    """
    target = Web3.to_checksum_address(address)
    balance = w3.eth.get_balance(target)
    if balance > 0:
        print(f"  funded already {address} ({balance / 1e18:.6f} AVAX)")
        return

    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas", 0) or 0
    # Same floor as every other transaction in this repo. A tip priced from the
    # public RPC's suggestion is accepted and then never mined.
    priority = max(w3.eth.max_priority_fee, MIN_PRIORITY_FEE_WEI)
    transaction: dict[str, Any] = {
        "type": 2,
        "chainId": chain_id,
        "nonce": w3.eth.get_transaction_count(Web3.to_checksum_address(admin.address)),
        "to": target,
        "value": w3.to_wei(amount, "ether"),
        "data": b"",
        "accessList": [],
        "gas": TRANSFER_GAS,
        "maxPriorityFeePerGas": priority,
        "maxFeePerGas": base_fee * 4 + priority,
    }
    tx_hash = send_raw_transaction(w3, sign_transaction(transaction, admin))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"  funded {amount} AVAX -> {address}  ({_link(chain_id, tx_hash)})")
    if receipt["status"] != 1:
        raise RuntimeError(f"funding {address} reverted")


def _grant(
    w3: Web3,
    registry: Any,
    admin: LocalSigner,
    name: str,
    role: bytes,
    address: str,
    chain_id: int,
) -> None:
    target = Web3.to_checksum_address(address)
    if registry.functions.hasRole(role, target).call():
        print(f"  {name} already held by {address}")
        return

    data = registry.encode_abi(abi_element_identifier="grantRole", args=[role, target])
    transaction = build_transaction(
        w3,
        sender=admin.address,
        to=registry.address,
        data=HexBytes(data),
        chain_id=chain_id,
    )
    tx_hash = send_raw_transaction(w3, sign_transaction(transaction, admin))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] != 1:
        raise RuntimeError(f"granting {name} to {address} reverted")
    print(f"  granted {name} -> {address}  ({_link(chain_id, tx_hash)})")


def _revoke_old(w3: Web3, registry: Any, admin: LocalSigner, deployment: Any) -> None:
    """Remove the EOA holders, once the KMS ones are confirmed.

    Only reachable after the verification loop above passed, so this can never
    leave the contract with no holder for a role.
    """
    print("\nrevoking the old EOA roles")
    for name, role, address in (
        ("REGISTRAR_ROLE", REGISTRAR_ROLE, deployment.registrar),
        ("SETTLER_ROLE", SETTLER_ROLE, deployment.settler),
    ):
        target = Web3.to_checksum_address(address)
        if not registry.functions.hasRole(role, target).call():
            print(f"  {name} was not held by {address}")
            continue
        data = registry.encode_abi(
            abi_element_identifier="revokeRole", args=[role, target]
        )
        transaction = build_transaction(
            w3,
            sender=admin.address,
            to=registry.address,
            data=HexBytes(data),
            chain_id=deployment.chain_id,
        )
        tx_hash = send_raw_transaction(w3, sign_transaction(transaction, admin))
        w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"  revoked {name} from {address}  ({_link(deployment.chain_id, tx_hash)})")


def _rewrite_deployment(targets: list[tuple[str, bytes, str]]) -> None:
    """Point the deployment record at the new holders.

    `check_deployment` compares a signer's address against this file and refuses
    to start on a mismatch, so leaving it stale would mean the cutover succeeds
    onchain and every service still refuses to boot.
    """
    record = json.loads(DEPLOYMENT_FILE.read_text())
    record["registrar"] = Web3.to_checksum_address(targets[0][2])
    record["settler"] = Web3.to_checksum_address(targets[1][2])
    record["note"] = (
        "Roles held by KMS keys (alias/trustrail-registrar, alias/trustrail-settler). "
        "No private key material outside KMS."
    )
    DEPLOYMENT_FILE.write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nrewrote {DEPLOYMENT_FILE.relative_to(REPO_ROOT)}")


def _banner(deployment: Any, admin: LocalSigner, targets: list, w3: Web3, *, dry_run: bool) -> None:
    print(f"network   {deployment.network} (chain {deployment.chain_id})")
    print(f"registry  {deployment.mandate_registry}")
    print(f"admin     {admin.address}  ({w3.eth.get_balance(Web3.to_checksum_address(admin.address)) / 1e18:.6f} AVAX)")
    if dry_run:
        print("mode      DRY RUN\n")
    else:
        print()
    print("granting")
    for name, _role, address in targets:
        current = w3.eth.get_balance(Web3.to_checksum_address(address)) / 1e18
        print(f"  {name:<15} -> {address}  ({current:.6f} AVAX)")
    print("\nleaving in place (revoke later with --revoke-old)")
    print(f"  REGISTRAR_ROLE  -> {deployment.registrar}")
    print(f"  SETTLER_ROLE    -> {deployment.settler}")


def _link(chain_id: int, tx_hash: Any) -> str:
    return transaction_url(chain_id, tx_hash.hex()) or tx_hash.hex()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", default="avalanche")
    parser.add_argument("--rpc", default="https://api.avax.network/ext/bc/C/rpc")
    parser.add_argument("--fund", type=float, default=FUNDING_AVAX)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--revoke-old",
        action="store_true",
        help="also remove the EOA holders, after the KMS ones are confirmed",
    )
    return parser.parse_args()


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
