"""The mandate digest, as EIP-712 typed data.

This is what the issuer key signs and what the MandateRegistry contract
recomputes in Solidity before it will move XSGD. Offchain and onchain
enforcement therefore agree by construction: there is one digest, not an
offchain hash the contract has to take on trust.

The struct is fully static — no dynamic arrays, no nested structs — so the ABI
encoding is just a run of 32-byte words. Hand-rolling that is a dozen lines and
saves pulling in a full ABI codec.

Unbound fields encode as their zero values. That is the honest encoding of "the
human approved a budget and an intent, not a SKU": at mint there genuinely is no
merchant and no basket. `MandateService.bind` fills them in and re-signs, which
produces a different digest.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from trustrail.models.mandate import Mandate
from trustrail.models.primitives import (
    ZERO_ADDRESS,
    ZERO_HASH,
    HexAddress,
    to_bytes,
)
from trustrail.signing.crypto import hash_bytes

#: Chain ids for the two Avalanche C-Chain environments. Cutover is config.
FUJI_CHAIN_ID = 43113
AVALANCHE_MAINNET_CHAIN_ID = 43114

_DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,"
    "address verifyingContract)"
)
_MANDATE_TYPE = (
    "Mandate(bytes32 mandateId,address principal,string agentId,"
    "uint256 maxAmount,string currency,uint64 expiresAt,string intent,"
    "address merchantAddress,bytes32 basketHash,bytes32 nonce)"
)
_EIP712_PREFIX = b"\x19\x01"


class Eip712Domain(BaseModel):
    """Domain separator inputs.

    `verifying_contract` stays at the zero address until track C deploys the
    MandateRegistry; pointing it at the deployed address is a config change, not
    a code change. The same is true of moving from Fuji to mainnet.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "TrustRail"
    version: str = "1"
    chain_id: int = FUJI_CHAIN_ID
    verifying_contract: HexAddress = ZERO_ADDRESS

    def separator(self) -> bytes:
        return _keccak_words(
            _type_hash(_DOMAIN_TYPE),
            _encode_string(self.name),
            _encode_string(self.version),
            _encode_uint(self.chain_id),
            _encode_address(self.verifying_contract),
        )


def mandate_digest(mandate: Mandate, domain: Eip712Domain) -> str:
    """The 32-byte digest signed by the issuer and recomputed by the contract."""
    struct_hash = _keccak_words(
        _type_hash(_MANDATE_TYPE),
        _encode_bytes32(mandate.mandate_id),
        _encode_address(mandate.principal),
        _encode_string(mandate.agent_id),
        _encode_uint(mandate.max_amount.minor_units),
        _encode_string(mandate.max_amount.currency),
        _encode_uint(int(mandate.expires_at.timestamp())),
        _encode_string(mandate.intent),
        _encode_address(mandate.merchant_address or ZERO_ADDRESS),
        _encode_bytes32(mandate.basket_hash or ZERO_HASH),
        _encode_bytes32(mandate.nonce),
    )
    return hash_bytes(_EIP712_PREFIX + domain.separator() + struct_hash)


def _keccak_words(*words: bytes) -> bytes:
    return to_bytes(hash_bytes(b"".join(words)))


def _type_hash(type_string: str) -> bytes:
    return to_bytes(hash_bytes(type_string.encode()))


def _encode_string(value: str) -> bytes:
    """Dynamic types are encoded as the keccak hash of their contents."""
    return to_bytes(hash_bytes(value.encode()))


def _encode_uint(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _encode_address(value: str) -> bytes:
    return bytes(12) + to_bytes(value)


def _encode_bytes32(value: str) -> bytes:
    return to_bytes(value)
