"""The settlement token: balances, allowances, and granting one.

`MandateRegistry` never custodies funds. `spend` calls
``transferFrom(principal, merchant, amount)``, which means the buyer keeps their
XSGD in their own wallet and grants the contract an allowance instead. Two
things therefore have to be true before any purchase can settle, and neither is
something our services can arrange on the buyer's behalf:

  1. the principal holds at least the charge amount in XSGD;
  2. the principal has approved MandateRegistry for at least that much.

Checking them explicitly is worth a round trip. Without it, an unfunded buyer
produces a bare ERC-20 revert that decodes to nothing our error table knows, and
the dashboard shows "unknown revert" for what is really "you have not topped up".

The ABI is written out rather than loaded from build artifacts. ERC-20 is a
fixed standard, and the real XSGD on mainnet was not compiled by us, so there is
no artifact to read.
"""

from __future__ import annotations

import logging

from hexbytes import HexBytes
from web3 import Web3

from trustrail.ports import Signer
from trustrail.settlement.chain.transactions import (
    build_transaction,
    send_raw_transaction,
    sign_transaction,
)

logger = logging.getLogger(__name__)

#: Just the five functions this system needs, plus `mint` for MockXSGD on
#: testnet. Calling `mint` against real XSGD reverts, which is correct.
ERC20_ABI: list[dict] = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name": "symbol",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "mint",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
]


class TokenClient:
    """Reads balances and allowances; writes only on behalf of a signer it is given."""

    def __init__(self, w3: Web3, token_address: str, chain_id: int) -> None:
        self._w3 = w3
        self._chain_id = chain_id
        self._contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )

    @property
    def address(self) -> str:
        return self._contract.address

    def decimals(self) -> int:
        return self._contract.functions.decimals().call()

    def symbol(self) -> str:
        return self._contract.functions.symbol().call()

    def balance_of(self, account: str) -> int:
        return self._contract.functions.balanceOf(
            Web3.to_checksum_address(account)
        ).call()

    def allowance(self, owner: str, spender: str) -> int:
        return self._contract.functions.allowance(
            Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)
        ).call()

    def approve(self, signer: Signer, spender: str, amount: int) -> str:
        """Grant `spender` an allowance over the signer's balance.

        This is the buyer's transaction, not ours — it is the one moment the
        principal's own key has to act, and it is a one-time setup rather than
        something per purchase. In a product that is a wallet prompt; in the
        demo it is a funded key we hold.
        """
        return self._send(
            signer, "approve", (Web3.to_checksum_address(spender), amount)
        )

    def mint(self, signer: Signer, to: str, amount: int) -> str:
        """Testnet only. MockXSGD leaves mint unrestricted; real XSGD reverts."""
        return self._send(signer, "mint", (Web3.to_checksum_address(to), amount))

    def _send(self, signer: Signer, name: str, args: tuple) -> str:
        transaction = build_transaction(
            self._w3,
            sender=Web3.to_checksum_address(signer.address),
            to=self._contract.address,
            data=HexBytes(
                self._contract.encode_abi(abi_element_identifier=name, args=list(args))
            ),
            chain_id=self._chain_id,
        )
        tx_hash = send_raw_transaction(
            self._w3, sign_transaction(transaction, signer)
        )
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            raise RuntimeError(f"token {name} reverted in {tx_hash.hex()}")
        return tx_hash.hex()
