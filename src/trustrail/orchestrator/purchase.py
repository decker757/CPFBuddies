"""The Purchase Orchestrator: the one place the whole flow is sequenced.

CLAUDE.md's runtime flow, in one method. Mint a mandate, let the Browser Agent
shop, let the Evaluator judge what it found, assemble everything the Verifier
needs, and act on the answer: PASS goes to the settlement queue, REVIEW pauses
for a human, FAIL stops.

Two things about this file carry most of its weight.

**It does every lookup the Verifier is not allowed to do.** Mandate state,
merchant record, evaluator record, kill-switch state, the current time — all of
it is gathered here and handed over as one payload. That is what buys the
Verifier its purity, and the reason the component that has to be defensible
under questioning is also the one with no I/O in it.

**It passes untrusted values through untouched.** The candidate's claimed payout
address goes onto the charge exactly as the listing gave it. Normalising it
against the registry here would turn the Verifier's payout check into a
comparison of a value with itself — the code would still look right, every test
that does not specifically model a scam sub-seller would still pass, and the
protection would be gone. Untrusted input is checked, never corrected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from trustrail.clock import SystemClock
from trustrail.errors import TrustRailError
from trustrail.mandate.service import MandateService
from trustrail.models.candidate import PurchaseCandidate
from trustrail.models.charge import Charge
from trustrail.models.evaluation import SignedEvaluatorOutput
from trustrail.models.mandate import (
    MandateBinding,
    MandateRecord,
    MandateState,
)
from trustrail.models.money import Money
from trustrail.models.primitives import new_hex32
from trustrail.models.review import (
    HumanApproval,
    ReviewHold,
    ReviewOutcome,
)
from trustrail.models.verdict import Decision, Verdict
from trustrail.models.verification import VerificationRequest
from trustrail.orchestrator.ports import ShoppingAgents
from trustrail.ports import (
    AgentDirectory,
    Clock,
    MerchantDirectory,
    ReviewHoldStore,
)
from trustrail.settlement.models import SettlementRequest
from trustrail.settlement.queue.base import SettlementQueue
from trustrail.verifier.config import VerifierConfig
from trustrail.verifier.service import VerifierService

logger = logging.getLogger(__name__)

DEFAULT_ACTOR = "purchase-orchestrator"


class ReviewNotFound(TrustRailError):
    """No held charge with that id. It was never held, or it has been resolved."""


class ReviewNotPending(TrustRailError):
    """The hold has already been answered, or its deadline has passed."""


@dataclass(frozen=True, slots=True)
class PurchaseOutcome:
    """What happened to one purchase attempt, in full.

    Carries the verdict rather than a boolean because every surface downstream
    wants the reasons: the dashboard renders them, the approval UI explains
    itself with them, and a FAIL nobody can point to afterwards is not an
    auditable system.
    """

    verdict: Verdict
    charge: Charge
    mandate: MandateRecord
    hold: ReviewHold | None = None
    queued_message_id: str | None = None

    @property
    def decision(self) -> Decision:
        return self.verdict.decision

    @property
    def settling(self) -> bool:
        """True once the charge is on the queue and money is about to move."""
        return self.queued_message_id is not None


class PurchaseOrchestrator:
    """Entry point for a purchase intent, and for resolving what it held."""

    def __init__(
        self,
        *,
        mandates: MandateService,
        verifier: VerifierService,
        config: VerifierConfig,
        shopper: ShoppingAgents,
        merchants: MerchantDirectory,
        agents: AgentDirectory,
        holds: ReviewHoldStore,
        queue: SettlementQueue,
        clock: Clock | None = None,
    ) -> None:
        self._mandates = mandates
        self._verifier = verifier
        self._config = config
        self._shopper = shopper
        self._merchants = merchants
        self._agents = agents
        self._holds = holds
        self._queue = queue
        self._clock = clock or SystemClock()

    async def purchase(
        self,
        *,
        principal: str,
        agent_id: str,
        intent: str,
        max_amount: Money,
        ttl: timedelta,
    ) -> PurchaseOutcome:
        """Run one purchase intent from the buyer's words to a verdict.

        The mandate is minted before anything is shopped for, so it commits to a
        budget and an intent and nothing else. Closing that gap is the
        Evaluator's job, and checking the Evaluator's work is the Verifier's.
        """
        # The registry knows which address this agent signs under; the Mandate
        # Service does not, and should not have to ask. Resolving it here is
        # what puts the acting agent, rather than the payer, in the onchain
        # record.
        agent = self._agents.get(agent_id)
        record = self._mandates.mint(
            principal=principal,
            agent_id=agent_id,
            max_amount=max_amount,
            intent=intent,
            ttl=ttl,
            agent_address=agent.address if agent else None,
        )
        mandate_id = record.mandate_id

        found = await self._shopper.shop(
            intent=intent, max_amount=max_amount, mandate_id=mandate_id
        )
        charge = _charge_for(found.candidate, mandate_id=mandate_id)
        evidence = found.evaluation

        verdict = self._verify(record, charge, evidence)
        return self._dispatch(record, charge, evidence, verdict)

    def pending_reviews(self) -> list[ReviewHold]:
        """Charges still waiting on a human. Backs the REVIEW approval surface."""
        return self._holds.list_pending(self._clock.now())

    def get_review(self, charge_id: str) -> ReviewHold | None:
        return self._holds.get(charge_id)

    def approve_review(self, charge_id: str, *, approved_by: str) -> PurchaseOutcome:
        """A human accepts the Evaluator's findings on a held charge.

        Approval binds the mandate to this merchant and this basket, re-signs
        it, and sends it back through the Verifier. It does not skip
        verification — it supplies more to verify, and the re-run is what
        catches a mandate that expired or was revoked while the human thought
        about it.

        The verdict this produces still says REVIEW: the risk score has not
        changed and pretending otherwise would put a false PASS in the audit
        log. What changes is that the request now carries a named approval,
        which is what makes it settleable.
        """
        hold = self._pending_or_raise(charge_id)
        mandate_id = hold.mandate_id

        merchant = self._merchants.get(hold.charge.merchant_id)
        if merchant is None:
            # Deregistered while held. Re-verification would FAIL on
            # MERCHANT_NOT_REGISTERED anyway; failing here keeps us from
            # binding a mandate to an address we no longer stand behind.
            raise ReviewNotPending(
                f"merchant {hold.charge.merchant_id} is no longer registered"
            )

        # Bind to the *registered* address, never the one the listing claimed.
        # The charge keeps the claimed address, so the payout check still has
        # two independently sourced values to compare.
        record = self._mandates.bind(
            mandate_id,
            MandateBinding(
                merchant_address=merchant.payout_address,
                basket_hash=hold.charge.basket_hash,
            ),
            approved_by=approved_by,
        )

        verdict = self._verify(record, hold.charge, hold.evaluation)
        approval = HumanApproval(
            charge_id=charge_id,
            approved_by=approved_by,
            approved_at=self._clock.now(),
        )
        resolved = hold.resolve(outcome=ReviewOutcome.APPROVED, by=approved_by)
        self._holds.put(resolved)

        message_id = self._enqueue(record, hold.charge, verdict, approval=approval)
        return PurchaseOutcome(
            verdict=verdict,
            charge=hold.charge,
            mandate=record,
            hold=resolved,
            queued_message_id=message_id,
        )

    def kill_review(self, charge_id: str, *, killed_by: str) -> PurchaseOutcome:
        """A human rejects a held charge. The mandate dies with it.

        Revoking rather than merely resolving the hold is deliberate: the buyer
        said no to this purchase, and leaving a live mandate behind would let a
        retry pick a different product under authority the human just withdrew.
        """
        hold = self._pending_or_raise(charge_id)
        record = self._mandates.revoke(
            hold.mandate_id, actor=killed_by, reason="rejected during review"
        )
        resolved = hold.resolve(outcome=ReviewOutcome.KILLED, by=killed_by)
        self._holds.put(resolved)
        return PurchaseOutcome(
            verdict=hold.verdict,
            charge=hold.charge,
            mandate=record,
            hold=resolved,
        )

    # -- internals ---------------------------------------------------------

    def _verify(
        self,
        record: MandateRecord,
        charge: Charge,
        evidence: SignedEvaluatorOutput,
    ) -> Verdict:
        """Assemble everything the Verifier needs and ask it.

        Every lookup in this method is one the Verifier is deliberately unable
        to perform for itself.
        """
        mandate = record.signed.mandate
        request = VerificationRequest(
            signed_mandate=record.signed,
            charge=charge,
            evaluation=evidence,
            mandate_state=MandateState(
                status=record.status,
                nonce_claimed_by=self._mandates.nonce_owner(mandate.nonce),
            ),
            merchant=self._merchants.get(charge.merchant_id),
            evaluator=self._agents.get(evidence.evaluation.evaluator_id),
            kill_switch_active=self._mandates.is_halted(mandate.principal),
            now=self._clock.now(),
        )
        verdict = self._verifier.verify(request)
        self._mandates.record_verdict(verdict)
        return verdict

    def _dispatch(
        self,
        record: MandateRecord,
        charge: Charge,
        evidence: SignedEvaluatorOutput,
        verdict: Verdict,
    ) -> PurchaseOutcome:
        """Act on the verdict. The only place a decision becomes a consequence."""
        if verdict.decision is Decision.REVIEW:
            hold = self._hold(record, charge, evidence, verdict)
            return PurchaseOutcome(
                verdict=verdict, charge=charge, mandate=record, hold=hold
            )

        message_id = self._enqueue(record, charge, verdict)
        return PurchaseOutcome(
            verdict=verdict,
            charge=charge,
            mandate=record,
            queued_message_id=message_id,
        )

    def _hold(
        self,
        record: MandateRecord,
        charge: Charge,
        evidence: SignedEvaluatorOutput,
        verdict: Verdict,
    ) -> ReviewHold:
        now = self._clock.now()
        hold = ReviewHold(
            charge_id=charge.charge_id,
            mandate_id=record.mandate_id,
            verdict=verdict,
            charge=charge,
            evaluation=evidence,
            held_at=now,
            deadline=ReviewHold.deadline_for(
                now=now,
                mandate_expires_at=record.signed.mandate.expires_at,
                review_window=timedelta(seconds=self._config.review_hold_seconds),
            ),
        )
        self._holds.put(hold)
        return hold

    def _enqueue(
        self,
        record: MandateRecord,
        charge: Charge,
        verdict: Verdict,
        *,
        approval: HumanApproval | None = None,
    ) -> str | None:
        """Publish to the settlement queue, if this verdict may settle at all.

        `SettlementRequest.settleable` is the gate, and it lives on the model
        rather than here on purpose: the rule about what may become money
        should not be re-stated by every caller that wants to enqueue.
        """
        request = SettlementRequest(
            verdict=verdict,
            charge=charge,
            signed_mandate=record.signed,
            human_approval=approval,
        )
        if not request.settleable:
            return None
        message_id = self._queue.publish(request)
        logger.info(
            "charge queued for settlement",
            extra={
                "mandate_id": record.mandate_id,
                "charge_id": charge.charge_id,
                "decision": verdict.decision.value,
                "human_approved": approval is not None,
            },
        )
        return message_id

    def _pending_or_raise(self, charge_id: str) -> ReviewHold:
        """Fetch a hold that can still be answered, or explain why it cannot.

        A hold past its deadline is resolved EXPIRED here rather than by a
        sweeper. CLAUDE.md rules out an indefinite pending queue, and a lapsed
        REVIEW has to end up as a FAIL — which it does, because the deadline
        can never be later than the mandate's own expiry.
        """
        hold = self._holds.get(charge_id)
        if hold is None:
            raise ReviewNotFound(f"no held charge {charge_id}")
        if hold.outcome is not ReviewOutcome.PENDING:
            raise ReviewNotPending(f"charge {charge_id} is already {hold.outcome}")
        if self._clock.now() >= hold.deadline:
            self._holds.put(hold.resolve(outcome=ReviewOutcome.EXPIRED, by=DEFAULT_ACTOR))
            raise ReviewNotPending(
                f"charge {charge_id} passed its review deadline at "
                f"{hold.deadline.isoformat()}"
            )
        if not hold.approvable:
            # Deterministic failures never create a hold, so reaching this
            # means something upstream changed. Refusing loudly beats
            # discovering later that a fact was overridable after all.
            raise ReviewNotPending(
                f"charge {charge_id} failed deterministically and cannot be reviewed"
            )
        return hold


def _charge_for(candidate: PurchaseCandidate, *, mandate_id: str) -> Charge:
    """Turn what the Browser Agent found into a charge against this mandate.

    The charge id is generated here, not by the agent: it identifies this
    settlement attempt, and letting untrusted code choose it would let one
    attempt impersonate another in the audit trail.
    """
    return Charge(
        charge_id=new_hex32(),
        mandate_id=mandate_id,
        merchant_id=candidate.merchant_id,
        payout_address=candidate.payout_address,
        amount=candidate.amount,
        basket_hash=candidate.basket_hash,
        quote_id=candidate.quote_id,
        sku=candidate.sku,
        title=candidate.title,
        quantity=candidate.quantity,
    )
