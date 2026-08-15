"""Digests and signatures, shared by the Mandate Service and the Verifier.

Both services need to compute the same digest — one to sign it, the other to
check it — so the computation lives here rather than in either service. Neither
imports the other.
"""

from trustrail.signing.crypto import (
    hash_bytes,
    recover_address,
    sign_digest,
    signed_by,
)
from trustrail.signing.eip712 import (
    AVALANCHE_MAINNET_CHAIN_ID,
    FUJI_CHAIN_ID,
    Eip712Domain,
    mandate_digest,
)
from trustrail.signing.evidence import evaluation_digest
from trustrail.signing.local import LocalSigner

__all__ = [
    "AVALANCHE_MAINNET_CHAIN_ID",
    "FUJI_CHAIN_ID",
    "Eip712Domain",
    "LocalSigner",
    "evaluation_digest",
    "hash_bytes",
    "mandate_digest",
    "recover_address",
    "sign_digest",
    "signed_by",
]
