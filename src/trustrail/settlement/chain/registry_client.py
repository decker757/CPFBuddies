"""Typed access to the MandateRegistry contract.

Everything the settlement worker needs to know about the chain lives here. The worker itself
deals in rails and instructions and never touches web3.

Mandate ids are ``Hex32`` on the wire, so they are already the contract's ``bytes32`` key --
decoded, not hashed. The offchain id and the onchain key are literally the same value, which
means an operator reading a Snowtrace event sees the id they can look up in DynamoDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError

from trustrail.models.primitives import ZERO_ADDRESS, to_bytes
from trustrail.models.verdict import ReasonCode
from trustrail.ports import Signer
from trustrail.settlement.chain.deployment import Deployment, load_abi
from trustrail.settlement.chain.transactions import (
    GAS_BUFFER,
    build_transaction,
    send_raw_transaction,
    sign_transaction,
)

CONTRACT_NAME = "MandateRegistry"

# Contract custom errors mapped onto the shared vocabulary, so an onchain revert and an
# offchain rejection describe themselves the same way on the dashboard.
#
# `MandateNotFound` has no offchain equivalent -- the Verifier cannot reach a charge whose
# mandate does not exist -- so it surfaces by name with no reason code rather than being
# forced into an approximate one.
ERROR_TO_REASON: dict[str, ReasonCode] = {
    "MandateIsRevoked": ReasonCode.MANDATE_REVOKED,
    "MandateExpired": ReasonCode.MANDATE_EXPIRED,
    "MandateAlreadyConsumed": ReasonCode.MANDATE_ALREADY_CONSUMED,
    "MerchantMismatch": ReasonCode.MERCHANT_BINDING_MISMATCH,
    "AmountExceedsCap": ReasonCode.CHARGE_OVER_CAP,
}


@dataclass(frozen=True)
class RevertInfo:
    """Why the contract refused.

    ``error_name`` is always present when a revert was observed. ``reason_code`` is present
    only when the error has a wire-contract equivalent, so an undecodable revert can never be
    mistaken for success.
    """

    error_name: str | None
    reason_code: ReasonCode | None = None

    def __str__(self) -> str:
        return self.error_name or "unknown revert"


@dataclass(frozen=True)
class ChainResult:
    """Outcome of a transaction that was actually broadcast.

    A revert is a result, not an exception. CLAUDE.md's demo depends on showing a genuinely
    reverted transaction on Snowtrace, so the caller needs the hash either way.
    """

    tx_hash: str | None
    status: Literal["CONFIRMED", "REVERTED"]
    block_number: int = 0
    gas_used: int = 0
    revert: RevertInfo | None = None

    @property
    def confirmed(self) -> bool:
        return self.status == "CONFIRMED"


class MandateRegistryClient:
    """Calls MandateRegistry, signing with whatever :class:`Signer` it is given."""

    def __init__(self, w3: Web3, deployment: Deployment, signer: Signer) -> None:
        self._w3 = w3
        self._deployment = deployment
        self._signer = signer
        self._abi = load_abi(CONTRACT_NAME)
        self._contract = w3.eth.contract(
            address=Web3.to_checksum_address(deployment.mandate_registry), abi=self._abi
        )
        self._selectors = _error_selectors(self._abi)
        # The wire contract stores addresses lowercased; web3 insists on checksummed.
        # Convert once, here at the boundary.
        self._caller = Web3.to_checksum_address(signer.address)

    @property
    def address(self) -> str:
        return self._contract.address

    @property
    def token_decimals(self) -> int:
        return self._deployment.settlement_token_decimals

    # ---- reads -------------------------------------------------------------------------

    def is_spendable(self, mandate_id: str) -> bool:
        return self._contract.functions.isSpendable(to_bytes(mandate_id)).call()

    def get_mandate(self, mandate_id: str) -> dict[str, Any]:
        raw = self._contract.functions.getMandate(to_bytes(mandate_id)).call()
        keys = (
            "principal",
            "agent",
            "merchant",
            "cap",
            "expiresAt",
            "mandateHash",
            "revoked",
            "consumed",
            "exists",
        )
        return dict(zip(keys, raw, strict=True))

    def preflight_spend(
        self, mandate_id: str, merchant: str, amount: int, basket_hash: str
    ) -> RevertInfo | None:
        """Simulate a spend without broadcasting.

        ``None`` means it would succeed. Anything else is the reason it would not -- including
        an undecodable revert, which is still a refusal.
        """
        return self._simulate(*self._spend_call(mandate_id, merchant, amount, basket_hash))

    # ---- writes ------------------------------------------------------------------------

    def spend(
        self, mandate_id: str, merchant: str, amount: int, basket_hash: str
    ) -> ChainResult:
        """Broadcast a spend and wait for its receipt."""
        return self._send(*self._spend_call(mandate_id, merchant, amount, basket_hash))

    def register_mandate(
        self,
        mandate_id: str,
        principal: str,
        agent: str,
        merchant: str | None,
        cap: int,
        expires_at: int,
        mandate_digest: str,
    ) -> ChainResult:
        """Record a mandate onchain. Requires REGISTRAR_ROLE.

        ``merchant`` may be ``None`` when the product has not been chosen yet -- the mandate is
        minted before a product is selected -- and the contract binds it on first spend.
        ``mandate_digest`` is the EIP-712 digest from :class:`SignedMandate`.
        """
        return self._send(
            "registerMandate",
            (
                to_bytes(mandate_id),
                Web3.to_checksum_address(principal),
                Web3.to_checksum_address(agent),
                Web3.to_checksum_address(merchant) if merchant else ZERO_ADDRESS,
                cap,
                expires_at,
                to_bytes(mandate_digest),
            ),
        )

    def revoke(self, mandate_id: str) -> ChainResult:
        """Kill switch. Requires REGISTRAR_ROLE."""
        return self._send("revoke", (to_bytes(mandate_id),))

    # ---- internals ---------------------------------------------------------------------

    @staticmethod
    def _spend_call(
        mandate_id: str, merchant: str, amount: int, basket_hash: str
    ) -> tuple[str, tuple]:
        return "spend", (
            to_bytes(mandate_id),
            Web3.to_checksum_address(merchant),
            amount,
            to_bytes(basket_hash),
        )

    def _bind(self, name: str, args: tuple):
        return getattr(self._contract.functions, name)(*args)

    def _simulate(
        self, name: str, args: tuple, block_identifier: int | str = "latest"
    ) -> RevertInfo | None:
        try:
            self._bind(name, args).call(
                {"from": self._caller}, block_identifier=block_identifier
            )
        except ContractLogicError as error:
            return self._decode_revert(error)
        except Exception:  # noqa: BLE001 - a node may refuse a historical call
            return RevertInfo(error_name=None)
        return None

    def _send(self, name: str, args: tuple) -> ChainResult:
        transaction = build_transaction(
            self._w3,
            sender=self._caller,
            to=self._contract.address,
            data=HexBytes(
                self._contract.encode_abi(abi_element_identifier=name, args=list(args))
            ),
            chain_id=self._deployment.chain_id,
            gas=self._estimate_or_default(name, args),
        )
        raw = sign_transaction(transaction, self._signer)

        try:
            tx_hash = send_raw_transaction(self._w3, raw)
        except Exception as error:  # noqa: BLE001 - re-raised below unless it is a revert
            # Nodes disagree about doomed transactions. Public RPC generally accepts and mines
            # them, producing a receipt with status 0. Hardhat, and some providers, simulate
            # first and refuse to broadcast. A refusal is still the contract saying no, so it
            # must not be reported as a retryable transport error.
            #
            # Consequence for the demo: the reverted-transaction-on-Snowtrace moment cannot be
            # rehearsed against a local node. Use Fuji for that.
            revert = self._decode_revert(error)
            if revert.error_name is None:
                raise
            return ChainResult(tx_hash=None, status="REVERTED", revert=revert)

        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt["status"] == 1:
            return ChainResult(
                tx_hash=tx_hash.hex(),
                status="CONFIRMED",
                block_number=receipt["blockNumber"],
                gas_used=receipt["gasUsed"],
            )

        # A receipt carries a status flag but never a reason. Replaying the call at the
        # reverting block is the standard way to recover it.
        revert = self._simulate(name, args, block_identifier=receipt["blockNumber"])
        return ChainResult(
            tx_hash=tx_hash.hex(),
            status="REVERTED",
            block_number=receipt["blockNumber"],
            gas_used=receipt["gasUsed"],
            revert=revert or RevertInfo(error_name=None),
        )

    def _estimate_or_default(self, name: str, args: tuple, fallback: int = 200_000) -> int:
        """Estimate gas, falling back to a fixed limit for transactions that will revert.

        A doomed transaction cannot be estimated. We still broadcast it, because the revert
        landing onchain is what the demo shows.
        """
        try:
            estimate = self._bind(name, args).estimate_gas({"from": self._caller})
        except (ContractLogicError, ValueError):
            return fallback
        return int(estimate * GAS_BUFFER)

    def _decode_revert(self, error: Exception) -> RevertInfo:
        """Recover the custom error name from whatever shape the node reported.

        Nodes disagree here. Public RPC returns ``data`` as a hex string; Hardhat returns a
        dict with the hex nested under its own ``data`` key and a human-readable ``message``.
        Try the selector first because it is exact, then fall back to the message so local
        development and mainnet behave the same.
        """
        selector = _revert_selector(error)
        name = self._selectors.get(selector) if selector else None
        if name is None:
            name = self._error_name_in_message(error)
        if name is None:
            return RevertInfo(error_name=None)
        return RevertInfo(error_name=name, reason_code=ERROR_TO_REASON.get(name))

    def _error_name_in_message(self, error: Exception) -> str | None:
        message = str(getattr(error, "message", "") or error)
        # Match "Name(" rather than bare "Name" so one error cannot be mistaken for another.
        return next(
            (name for name in self._selectors.values() if f"{name}(" in message), None
        )


def _revert_selector(error: Exception) -> str | None:
    """Pull the 4-byte error selector out of a revert, whichever shape the node used."""
    data: Any = getattr(error, "data", None)
    if data is None and error.args and isinstance(error.args[0], dict):
        # Web3RPCError carries the whole JSON-RPC error object as its first argument.
        data = error.args[0].get("data")
    if isinstance(data, dict):
        data = data.get("data")
    if isinstance(data, (bytes, bytearray)):
        data = "0x" + bytes(data).hex()
    if isinstance(data, str) and data.startswith("0x") and len(data) >= 10:
        return data[:10].lower()
    return None


def _error_selectors(abi: list[dict]) -> dict[str, str]:
    """Selector -> error name, derived from the ABI so it cannot drift from the contract."""
    selectors: dict[str, str] = {}
    for item in abi:
        if item.get("type") != "error":
            continue
        signature = f"{item['name']}({','.join(i['type'] for i in item['inputs'])})"
        selectors["0x" + keccak(text=signature)[:4].hex()] = item["name"]
    return selectors
