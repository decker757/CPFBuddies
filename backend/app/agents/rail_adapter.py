"""Workstream B behind workstream A's shopping port.

The Purchase Orchestrator asks for a candidate and signed evidence. This is
where that request becomes the two agents actually running: the Browser Agent
picks from live `/listings` responses, and the Evaluator judges what it picked
and signs the finding with its own key.

The direction of the dependency is deliberate and only works one way. `app`
imports `trustrail`; `trustrail` never imports `app`. That is what lets the
agents be a separate deployable, which is what CLAUDE.md means when it says the
Browser Agent is assumed compromisable — it does not share a process with the
service that assembles what the Verifier sees.

Note what this adapter does *not* do. It does not check the price against the
cap, drop a listing that looks poisoned, or repair a payout address that
disagrees with the registry. Every one of those is the Verifier's, and doing
any of them here would move a check inside the blast radius of a compromised
agent.
"""

from __future__ import annotations

from trustrail.models.candidate import PurchaseCandidate
from trustrail.models.money import Money
from trustrail.orchestrator.ports import ShoppingResult
from trustrail.ports import Signer

from app.agents.browser import BrowserAgent
from app.agents.evaluator import EvaluatorAgent
from app.contracts import CandidateSelection
from app.evidence import to_signed_evidence


class BrowserAndEvaluator:
    """B's two agents, presented to the orchestrator as one round trip."""

    def __init__(
        self,
        *,
        browser: BrowserAgent,
        evaluator: EvaluatorAgent,
        signer: Signer,
        preferred_sku: str | None = None,
    ) -> None:
        """`signer` holds the Evaluator's key.

        It must be the key registered for the evaluator in the Agent Registry,
        and it belongs to the Evaluator alone — the Browser Agent has no key,
        which is exactly why it cannot write itself a clean risk score.

        `preferred_sku` pins the Browser Agent to one product. It is how the
        demo chooses which listing to run against, because selection is by
        lowest price and the deliberately suspicious S$0.50 listing would
        otherwise win every time. Leave it None for real selection.
        """
        self._browser = browser
        self._evaluator = evaluator
        self._signer = signer
        self._preferred_sku = preferred_sku

    async def shop(
        self, *, intent: str, max_amount: Money, mandate_id: str
    ) -> ShoppingResult:
        selection = await self._browser.find_candidate(
            intent=intent,
            max_price=max_amount,
            preferred_sku=self._preferred_sku,
        )
        # The Evaluator reads the listing the Browser Agent chose, not a
        # summary of it: injection lives in the description, and a summary is
        # where it would get lost.
        findings = self._evaluator.evaluate(
            listing=selection.listing, intent=intent, max_amount=max_amount
        )
        evidence = to_signed_evidence(
            findings,
            mandate_id=mandate_id,
            basket_hash=selection.basket_hash,
            amount=selection.listing.price,
            signer=self._signer,
        )
        return ShoppingResult(
            candidate=_candidate_for(selection), evaluation=evidence
        )


def _candidate_for(selection: CandidateSelection) -> PurchaseCandidate:
    """Map B's selection onto the shape the orchestrator charges against.

    `payout_address` is copied from the merchant block of the listings
    response — the address the platform *claimed*, carried forward exactly as
    received. The Verifier compares it against the Merchant Registry, so
    substituting the registered address here would leave that check comparing a
    value with itself and silently delete the sub-seller protection.
    """
    listing = selection.listing
    return PurchaseCandidate(
        merchant_id=selection.merchant.id,
        payout_address=selection.merchant.address,
        amount=listing.price,
        basket_hash=selection.basket_hash,
        quote_id=selection.quote_id,
        sku=listing.sku,
        title=listing.title,
        quantity=1,
    )
