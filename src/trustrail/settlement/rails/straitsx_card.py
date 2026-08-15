"""The fallback rail: a one-time StraitsX card scoped to the approved amount.

NOT WIRED. No StraitsX credentials yet, so this ships as a local fake behind the real protocol.
The interface is the deliverable; swapping in MCP calls should not change anything above it.

Design intent when it is wired: mint a single-use card with its limit set to the **approved
price**, not the mandate cap. The card's own limit becomes a second enforcement point, so a
compromised settlement worker still cannot charge more than the approved amount.

Coverage note for the pitch: this rail cannot bind a basket hash, so it offers degraded
enforcement -- cap, merchant and expiry, but no basket binding. That is the coverage gradient
CLAUDE.md describes, not a wall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from trustrail.models.money import Currency
from trustrail.models.audit import SettlementOutcome
from trustrail.settlement.models import SettlementInstruction, SettlementReceipt

logger = logging.getLogger(__name__)

RAIL_NAME = "straitsx-card"


@dataclass(frozen=True)
class IssuedCard:
    """A one-time card as the fake models it."""

    card_id: str
    mandate_id: str
    limit_minor_units: int
    currency: Currency
    merchant_id: str


class StraitsXCardRail:
    """Local fake for the StraitsX card-issuing MCP.

    Records what it would have issued so the full settlement path is exercisable without
    credentials. Replace the body of :meth:`_issue_card` with the real MCP call; nothing else
    should need to change.
    """

    def __init__(self) -> None:
        self._issued: list[IssuedCard] = []

    @property
    def name(self) -> str:
        return RAIL_NAME

    @property
    def issued(self) -> list[IssuedCard]:
        """Cards this fake has minted. Test and demo affordance only."""
        return list(self._issued)

    def settle(self, instruction: SettlementInstruction) -> SettlementReceipt:
        try:
            card = self._issue_card(instruction)
        except Exception as error:  # noqa: BLE001 - a card API fault is retryable
            logger.exception("card issuance failed for %s", instruction.mandate_id)
            return SettlementReceipt(
                mandate_id=instruction.mandate_id,
                rail=self.name,
                status=SettlementOutcome.ERROR,
                detail=f"{type(error).__name__}: {error}",
            )

        logger.info(
            "issued fake one-time card %s for mandate %s limited to %s",
            card.card_id,
            instruction.mandate_id,
            instruction.amount,
        )
        return SettlementReceipt(
            mandate_id=instruction.mandate_id,
            rail=self.name,
            status=SettlementOutcome.SETTLED,
            reference=card.card_id,
            detail="TODO: fake card rail, no real funds moved",
        )

    def _issue_card(self, instruction: SettlementInstruction) -> IssuedCard:
        # TODO: replace with the StraitsX card-issuing MCP call once credentials land.
        card = IssuedCard(
            card_id=f"card_fake_{len(self._issued) + 1}",
            mandate_id=instruction.mandate_id,
            limit_minor_units=instruction.amount.minor_units,
            currency=instruction.amount.currency,
            merchant_id=instruction.merchant_id,
        )
        self._issued.append(card)
        return card
