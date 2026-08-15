"""Building, signing and sending EIP-1559 transactions with a :class:`trustrail.ports.Signer`.

Kept separate from any particular contract so every contract call shares one encoding path.
``eth_account`` can only sign with a key it holds, so a KMS-backed signer needs this split:
hash the unsigned transaction, sign the digest, re-encode with ``(v, r, s)``.

This module owns the one adapter between the two signature conventions in play. The ``Signer``
port returns a 65-byte ``r || s || v`` string with ``v`` in {27, 28} -- the Ethereum
convention, which is what a contract verifies. A type-2 transaction instead carries
``y_parity`` in {0, 1}. Converting in exactly one place is why there is only one Signer port
in this repo.
"""

from __future__ import annotations

from typing import Any

from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from web3 import Web3

from trustrail.models.primitives import to_bytes
from trustrail.ports import Signer

SIGNATURE_BYTES = 65
_V_OFFSET = 27

# Gas headroom over the node's estimate. Estimation runs against current state; by the time the
# transaction lands, storage may have changed enough to cost slightly more.
GAS_BUFFER = 1.25

#: Floor for the priority fee, in wei. Avalanche's public RPC has been observed
#: suggesting ~1e-9 gwei, which is indistinguishable from offering nothing: the
#: transaction is accepted into the mempool and then simply never included. That
#: failure is nasty because it does not look like a failure -- there is a hash,
#: no error, and a client that waits for a receipt waits forever.
#:
#: One gwei is a rounding error here (a base fee of ~0.08 gwei makes a 150k-gas
#: call cost a small fraction of a cent) and it makes inclusion unambiguous.
MIN_PRIORITY_FEE_WEI = 1_000_000_000

#: How many times the current base fee to allow in `maxFeePerGas`. The base fee
#: moves between pricing a transaction and its landing, and a tight ceiling
#: strands it until the fee happens to fall back.
BASE_FEE_HEADROOM = 4


def to_y_parity_vrs(signature: str) -> tuple[int, int, int]:
    """Split a ``0x`` + r || s || v signature into ``(y_parity, r, s)``.

    The Signer port speaks the contract convention; type-2 transactions want y-parity.
    """
    raw = to_bytes(signature)
    if len(raw) != SIGNATURE_BYTES:
        raise ValueError(f"expected a 65-byte signature, got {len(raw)} bytes")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]
    y_parity = v - _V_OFFSET if v >= _V_OFFSET else v
    if y_parity not in (0, 1):
        raise ValueError(f"recovery id must be 0 or 1, got {y_parity}")
    return y_parity, r, s


def build_transaction(
    w3: Web3,
    *,
    sender: str,
    to: str,
    data: HexBytes | bytes,
    chain_id: int,
    gas: int | None = None,
) -> dict[str, Any]:
    """Assemble an unsigned type-2 transaction with fees taken from the current base fee."""
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas", 0) or 0
    # Never take the node's suggestion below the floor. See MIN_PRIORITY_FEE_WEI:
    # a near-zero tip produces a transaction that is accepted and then silently
    # never mined, which is the worst shape a failure can have.
    priority_fee = max(w3.eth.max_priority_fee, MIN_PRIORITY_FEE_WEI)

    transaction: dict[str, Any] = {
        "type": 2,
        "chainId": chain_id,
        "nonce": w3.eth.get_transaction_count(Web3.to_checksum_address(sender)),
        "to": Web3.to_checksum_address(to),
        "value": 0,
        "data": bytes(data),
        "accessList": [],
        "maxPriorityFeePerGas": priority_fee,
        "maxFeePerGas": base_fee * BASE_FEE_HEADROOM + priority_fee,
    }

    if gas is None:
        gas = int(
            w3.eth.estimate_gas(
                {**transaction, "from": Web3.to_checksum_address(sender)}
            )
            * GAS_BUFFER
        )
    transaction["gas"] = gas
    return transaction


def sign_transaction(transaction: dict[str, Any], signer: Signer) -> HexBytes:
    """Sign an unsigned transaction dict, returning raw bytes ready to broadcast."""
    digest = TypedTransaction.from_dict(transaction).hash()
    v, r, s = to_y_parity_vrs(signer.sign(digest))
    signed = TypedTransaction.from_dict({**transaction, "v": v, "r": r, "s": s})
    return HexBytes(signed.encode())


def send_raw_transaction(w3: Web3, raw: HexBytes) -> HexBytes:
    return w3.eth.send_raw_transaction(raw)
