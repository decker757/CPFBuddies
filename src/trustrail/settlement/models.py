"""Settlement-side shapes.

These are workstream C's, built on the wire contract rather than duplicating it: a
:class:`SettlementRequest` is exactly what the Purchase Orchestrator has in hand once the
Verifier answers -- the verdict, the charge it judged, and the signed mandate authorising it.

The Verifier's PASS is the only thing that opens this path. Nothing here re-decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from trustrail.models.audit import SettlementOutcome
from trustrail.models.charge import Charge
from trustrail.models.mandate import SignedMandate
from trustrail.models.money import Money
from trustrail.models.primitives import Hex32, HexAddress
from trustrail.models.verdict import Decision, ReasonCode, Verdict


class SettlementRequest(BaseModel):
    """What goes on the queue: a verdict, and everything needed to act on it.

    The verdict alone is not enough -- it carries ids, not amounts -- so the charge and the
    signed mandate travel with it. Carrying the signed mandate also means the worker can pass
    the issuer's digest to ``registerMandate`` without a second lookup.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: Verdict
    charge: Charge
    signed_mandate: SignedMandate

    @model_validator(mode="after")
    def _ids_must_agree(self) -> Self:
        """The three parts must describe the same purchase.

        A mismatch here would mean settling one charge against another's authorisation, which
        no downstream check would catch: the contract only sees the mandate id it is handed.
        """
        mandate_id = self.signed_mandate.mandate.mandate_id
        if self.charge.mandate_id != mandate_id:
            raise ValueError("charge references a different mandate than the one supplied")
        if self.verdict.mandate_id != mandate_id:
            raise ValueError("verdict references a different mandate than the one supplied")
        if self.verdict.charge_id != self.charge.charge_id:
            raise ValueError("verdict references a different charge than the one supplied")
        return self

    @property
    def settleable(self) -> bool:
        return self.verdict.decision is Decision.PASS


@dataclass(frozen=True)
class SettlementInstruction:
    """What to pay, to whom, under which mandate.

    Deliberately flat and rail-neutral: no chain concepts and no card concepts, so the same
    instruction can go to either rail.
    """

    mandate_id: Hex32
    charge_id: Hex32
    principal: HexAddress
    merchant_id: str
    payout_address: HexAddress
    amount: Money
    basket_hash: Hex32
    quote_id: str
    sku: str
    quantity: int

    @classmethod
    def from_request(cls, request: SettlementRequest) -> "SettlementInstruction":
        """Build from a Verifier PASS.

        Refuses anything else. The worker is not an orchestrator and must never be the place a
        REVIEW quietly becomes a payment.
        """
        if not request.settleable:
            raise ValueError(
                f"only PASS decisions are settleable, got "
                f"{request.verdict.decision.value} "
                f"({[code.value for code in request.verdict.reason_codes]})"
            )
        charge = request.charge
        return cls(
            mandate_id=charge.mandate_id,
            charge_id=charge.charge_id,
            principal=request.signed_mandate.mandate.principal,
            merchant_id=charge.merchant_id,
            payout_address=charge.payout_address,
            amount=charge.amount,
            basket_hash=charge.basket_hash,
            quote_id=charge.quote_id,
            sku=charge.sku,
            quantity=charge.quantity,
        )


@dataclass(frozen=True)
class SettlementReceipt:
    """What the rail did.

    ``REFUSED`` means the rail worked correctly and declined -- an onchain revert, a card
    authorisation denied. ``ERROR`` means the rail itself failed and the attempt can be
    retried. The worker treats these very differently, so keeping them distinct matters: a
    reverted transaction retried forever burns gas and never settles.
    """

    mandate_id: Hex32
    rail: str
    status: SettlementOutcome
    reference: str | None = None
    explorer_url: str | None = None
    reason_code: ReasonCode | None = None
    detail: str | None = None

    @property
    def settled(self) -> bool:
        return self.status is SettlementOutcome.SETTLED

    @property
    def retryable(self) -> bool:
        return self.status is SettlementOutcome.ERROR
