"""Shared scalar types for the wire contract.

Every hex value in this system is normalised to lowercase the moment it is
parsed. That is deliberate: the Verifier compares addresses and hashes for
equality (payout address binding, basket hash binding), and doing the
normalisation once at the edge means no comparison downstream has to remember
to be case-insensitive.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, StringConstraints

_ADDRESS_PATTERN = r"^0x[0-9a-fA-F]{40}$"
_HASH_PATTERN = r"^0x[0-9a-fA-F]{64}$"
_SIGNATURE_PATTERN = r"^0x[0-9a-fA-F]{130}$"

#: A 20-byte EVM address, lowercased. Used for principals, merchants, payouts.
HexAddress = Annotated[
    str,
    StringConstraints(pattern=_ADDRESS_PATTERN),
    AfterValidator(str.lower),
]

#: A 32-byte hash or identifier, lowercased. Mandate ids, nonces, basket hashes.
Hex32 = Annotated[
    str,
    StringConstraints(pattern=_HASH_PATTERN),
    AfterValidator(str.lower),
]

#: A 65-byte secp256k1 signature as r || s || v, lowercased.
HexSignature = Annotated[
    str,
    StringConstraints(pattern=_SIGNATURE_PATTERN),
    AfterValidator(str.lower),
]

#: A timezone-aware timestamp. Naive datetimes are rejected at the edge so no
#: comparison downstream can silently mix wall-clock and UTC.
Timestamp = AwareDatetime

#: Short free-text identifiers supplied by callers (agent ids, merchant ids).
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=64)]

#: Display text supplied by a merchant — product titles, platform names. Capped
#: because it is attacker-controlled: a 4000-word product title is itself a
#: signal, not something to store faithfully.
MerchantText = Annotated[str, StringConstraints(min_length=1, max_length=200)]

ZERO_ADDRESS: str = "0x" + "00" * 20
ZERO_HASH: str = "0x" + "00" * 32


def new_hex32() -> str:
    """Generate a random 32-byte identifier (mandate id, nonce)."""
    return "0x" + secrets.token_hex(32)


def to_bytes(value: str) -> bytes:
    """Decode a 0x-prefixed hex string into raw bytes."""
    return bytes.fromhex(value.removeprefix("0x"))


def clip_text(text: str, limit: int) -> str:
    """Fit text into a capped field without raising.

    For merchant- and Evaluator-supplied prose on its way into a shorter field:
    ten Evaluator reasons at 280 characters each is a valid output and five
    times what an audit summary may hold. Truncating is right where rejecting is
    not — the structured payload alongside keeps the full text, so nothing is
    lost, and a merchant cannot fail a purchase by being verbose.
    """
    return text if len(text) <= limit else text[: limit - 1] + "…"
