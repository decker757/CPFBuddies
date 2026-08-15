"""The x402 client: request, get told to pay, pay, retry with proof.

Deliberately knows nothing about settlement. It is handed a ``pay`` callable that turns terms
into a proof, so the same client works over the onchain rail, the card rail, or a fake in
tests -- and ``x402`` never imports ``settlement``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

import httpx

from trustrail.models.charge import Charge
from trustrail.x402.terms import (
    PAYMENT_HEADER,
    PAYMENT_REQUIRED_STATUS,
    PaymentProof,
    PaymentTerms,
    encode_proof,
    parse_terms,
)

logger = logging.getLogger(__name__)


class PayCallable(Protocol):
    """Settles the given terms and returns proof, or raises if it could not."""

    def __call__(self, terms: PaymentTerms) -> PaymentProof:
        ...


@dataclass(frozen=True)
class PurchaseResult:
    """Outcome of a full x402 exchange."""

    status_code: int
    body: Any
    terms: PaymentTerms | None = None
    proof: PaymentProof | None = None

    @property
    def paid(self) -> bool:
        return self.proof is not None and 200 <= self.status_code < 300


class X402Client:
    """Drives the 402 handshake against a merchant's ``POST /purchase``."""

    def __init__(self, http: httpx.Client, clock: Callable[[], datetime] | None = None) -> None:
        self._http = http
        self._now = clock or (lambda: datetime.now(timezone.utc))

    def purchase(self, url: str, payload: dict, charge: Charge, pay: PayCallable) -> PurchaseResult:
        """POST, settle if asked to, then retry once with proof.

        Exactly one retry. A merchant that answers 402 twice is misbehaving, and looping would
        risk paying twice.
        """
        first = self._http.post(url, json=payload)
        if first.status_code != PAYMENT_REQUIRED_STATUS:
            return PurchaseResult(status_code=first.status_code, body=_body(first))

        terms = parse_terms(first.content)
        logger.info("402 from %s asking %s to %s", url, terms.amount, terms.pay_to)

        # Validate the merchant's terms against what was actually approved before paying.
        terms.assert_matches(charge)
        if terms.is_expired(self._now()):
            raise ValueError(f"payment terms expired at {terms.expires_at.isoformat()}")

        proof = pay(terms)
        second = self._http.post(
            url, json=payload, headers={PAYMENT_HEADER: encode_proof(proof)}
        )

        if second.status_code == PAYMENT_REQUIRED_STATUS:
            raise RuntimeError(
                "merchant returned 402 again after payment; not retrying to avoid double payment"
            )

        return PurchaseResult(
            status_code=second.status_code, body=_body(second), terms=terms, proof=proof
        )


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
