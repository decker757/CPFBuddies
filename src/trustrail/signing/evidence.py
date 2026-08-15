"""The Evaluator's output digest.

Deliberately *not* EIP-712. This payload is evidence for the offchain Verifier
and never reaches a contract, so there is nothing to recompute in Solidity and
no reason to pay the typed-data complexity for a struct containing a list of
free-text reasons.

Canonical JSON is enough, provided it is the only way the digest is ever
computed. Both the signer and the verifier call this function, so it is.
"""

from __future__ import annotations

from trustrail.canonical import canonical_json
from trustrail.models.evaluation import EvaluatorOutput
from trustrail.signing.crypto import hash_bytes


def evaluation_digest(evaluation: EvaluatorOutput) -> str:
    """keccak256 over the canonical JSON encoding of the evaluator's findings."""
    return hash_bytes(canonical_json(evaluation))
