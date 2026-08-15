"""Fixed keys and identifiers for the demo fixtures.

These are hardcoded so the fixtures are byte-reproducible: regenerating them
should produce no git diff unless the contract actually changed. That is what
makes `contracts/` a thing the other workstreams can rely on.

None of these keys are secret and none of them may ever hold value. The
production issuer key lives in KMS and is never materialised anywhere.
"""

from __future__ import annotations

from trustrail.models.primitives import to_bytes
from trustrail.signing.crypto import address_of, hash_bytes

#: The mandate issuer. In production this is the KMS key; here it is a constant
#: so every fixture signature is stable across regeneration.
ISSUER_PRIVATE_KEY = to_bytes("0x" + "11" * 32)

#: The registered Evaluator Agent, whose signature over its findings is what
#: stops a compromised Browser Agent scoring its own work.
EVALUATOR_PRIVATE_KEY = to_bytes("0x" + "22" * 32)

#: An unregistered key, used to forge an evaluator verdict in the fixtures.
IMPOSTOR_PRIVATE_KEY = to_bytes("0x" + "33" * 32)

ISSUER_ADDRESS = address_of(ISSUER_PRIVATE_KEY)
EVALUATOR_ADDRESS = address_of(EVALUATOR_PRIVATE_KEY)

EVALUATOR_ID = "evaluator-1"
BROWSER_AGENT_ID = "browser-1"
MERCHANT_ID = "mrc_sgmart"


def label_to_hash(label: str) -> str:
    """A stable 32-byte value from a readable label.

    Fixtures full of `0xaaaa...` teach the reader nothing. Deriving ids from
    labels means a basket hash in a fixture is traceable to the thing it stands
    for, while still looking like the opaque value it is in production.
    """
    return hash_bytes(label.encode())


def label_to_address(label: str) -> str:
    """A stable address from a readable label."""
    return "0x" + label_to_hash(label)[-40:]
