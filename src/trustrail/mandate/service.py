"""The Mandate Service: mint, bind, revoke, consume, kill switch.

This is the only service that signs. The issuer key lives in KMS and is reached
through the `Signer` port, so nothing else in the system — not the Browser
Agent, not the Evaluator, not the Verifier — is in a position to mint authority.

Every lifecycle change is a compare-and-set against the stored status, so two
workers racing to spend the same mandate cannot both win, and every change
writes an audit entry before it is considered done.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from trustrail.clock import SystemClock
from trustrail.errors import IllegalBinding, MandateNotFound, MandateStatusConflict
from trustrail.models.audit import AuditEntry, AuditEventType
from trustrail.models.evaluation import EvaluatorOutput
from trustrail.models.mandate import (
    Mandate,
    MandateBinding,
    MandateRecord,
    MandateStatus,
    SignedMandate,
)
from trustrail.models.money import Money
from trustrail.models.primitives import (
    ZERO_HASH,
    Timestamp,
    clip_text,
    new_hex32,
    to_bytes,
)
from trustrail.models.verdict import Decision, Verdict
from trustrail.ports import (
    AuditLog,
    Clock,
    KillSwitchStore,
    MandateRegistrar,
    MandateStore,
    Signer,
)
from trustrail.signing.eip712 import Eip712Domain, mandate_digest

logger = logging.getLogger(__name__)

#: Statuses a mandate can still move on from. Anything else is terminal.
_LIVE_STATUSES = (MandateStatus.MINTED, MandateStatus.BOUND)

#: Events that deserve a second look in a log stream. A rejected charge and a
#: withdrawn mandate are the two things an operator would want to find without
#: reading every line, and CloudWatch can filter on the level.
_NOTABLE_EVENTS = frozenset(
    {AuditEventType.MANDATE_REVOKED, AuditEventType.KILL_SWITCH_SET}
)


class MandateService:
    """Issues and manages the credentials that authorise spending."""

    def __init__(
        self,
        *,
        signer: Signer,
        store: MandateStore,
        kill_switch: KillSwitchStore,
        audit: AuditLog,
        domain: Eip712Domain | None = None,
        clock: Clock | None = None,
        registrar: MandateRegistrar | None = None,
    ) -> None:
        """`registrar` puts mandates on the ledger; omit it to run entirely offchain.

        Omitting it is what the unit tests and the offline demo do. It is not a
        mode a deployment should ever run in: without the contract, the cap and
        the expiry are enforced only by our own services, which is precisely
        the trust we tell people they do not have to extend to us.
        """
        self._signer = signer
        self._store = store
        self._kill_switch = kill_switch
        self._audit = audit
        self._domain = domain or Eip712Domain()
        self._clock = clock or SystemClock()
        self._registrar = registrar

    @property
    def issuer_address(self) -> str:
        """The address the Verifier must be configured to trust."""
        return self._signer.address

    def mint(
        self,
        *,
        principal: str,
        agent_id: str,
        max_amount: Money,
        intent: str,
        ttl: timedelta,
        agent_address: str | None = None,
    ) -> MandateRecord:
        """Issue a mandate for a budget and an intent.

        No merchant and no basket: at this point the buyer has said "toothbrush
        under $5" and nothing has been shopped for yet. Claiming basket-level
        binding here would be a lie — that comes later, at `bind`, or never,
        because the Verifier checks the intent against the evidence instead.
        """
        now = self._clock.now()
        mandate = Mandate(
            mandate_id=new_hex32(),
            principal=principal,
            agent_id=agent_id,
            max_amount=max_amount,
            expires_at=now + ttl,
            intent=intent,
            nonce=new_hex32(),
        )
        record = MandateRecord(
            signed=self._sign(mandate),
            status=MandateStatus.MINTED,
            created_at=now,
            updated_at=now,
        )
        # Local first: `save_new` claims the nonce and rejects a duplicate id,
        # both of which are cheap. Spending gas to register a mandate that the
        # store is about to refuse would be the wrong order.
        self._store.save_new(record)
        tx_hash = self._register_onchain(record, agent_address)
        self._log(
            record,
            AuditEventType.MANDATE_MINTED,
            actor=agent_id,
            summary=(
                f"minted for up to {max_amount}, expiring "
                f"{mandate.expires_at.isoformat()}"
                + (f", registered onchain in {tx_hash}" if tx_hash else "")
            ),
        )
        return record

    def bind(
        self, mandate_id: str, binding: MandateBinding, *, approved_by: str
    ) -> MandateRecord:
        """Commit a mandate to one merchant and one basket, and re-sign it.

        This is what a human approving a REVIEW does. It narrows the mandate and
        can only ever narrow it: `MandateBinding` has no field for the cap or the
        expiry, and the two fields it does have may only move from empty to set.
        The re-signed mandate then re-enters the Verifier — approval does not
        skip verification, it supplies more to verify.
        """
        record = self._require(mandate_id)
        mandate = record.signed.mandate
        if record.status is not MandateStatus.MINTED or mandate.is_bound:
            raise IllegalBinding(
                f"mandate {mandate_id} is {record.status} and cannot be bound again"
            )

        bound = mandate.model_copy(update=binding.model_dump())
        updated = record.evolve(
            status=MandateStatus.BOUND,
            at=self._clock.now(),
            signed=self._sign(bound),
        )
        self._store.replace(updated, expected_status=MandateStatus.MINTED)
        self._log(
            updated,
            AuditEventType.MANDATE_BOUND,
            actor=approved_by,
            summary=f"bound to merchant {binding.merchant_address}",
        )
        return updated

    def revoke(self, mandate_id: str, *, actor: str, reason: str) -> MandateRecord:
        """Kill one mandate. Revocation is a deterministic FAIL thereafter.

        Revoked offchain first, then onchain. The offchain record is what the
        Verifier reads, so it is what stops the next charge; the onchain
        revocation is what stops anyone who bypasses us entirely.
        """
        record = self._transition(
            mandate_id,
            to=MandateStatus.REVOKED,
            event=AuditEventType.MANDATE_REVOKED,
            actor=actor,
            summary=f"revoked: {reason}",
        )
        if self._registrar is None:
            return record

        # A second entry rather than one, because these are two different
        # guarantees and only one of them has happened above. The offchain
        # record stops the next charge through this system; the onchain
        # revocation stops anyone who bypasses it entirely. Ordering them the
        # other way round -- chain first, then the entry with its hash -- would
        # leave a mandate live locally whenever the chain call hung.
        tx_hash = self._registrar.revoke(mandate_id)
        if tx_hash:
            self._append_audit(
                mandate_id=mandate_id,
                event=AuditEventType.MANDATE_REVOKED,
                occurred_at=self._clock.now(),
                actor=actor,
                summary=f"revocation recorded onchain in {tx_hash}",
            )
        return record

    def consume(self, mandate_id: str, *, actor: str) -> MandateRecord:
        """Spend a mandate, exactly once.

        This is where replay protection actually lands. The Verifier reports
        that a mandate has already been consumed, but it is this compare-and-set
        that makes the statement true: two settlement workers racing on the same
        mandate means one of them raises `MandateStatusConflict`.
        """
        return self._transition(
            mandate_id,
            to=MandateStatus.CONSUMED,
            event=AuditEventType.MANDATE_CONSUMED,
            actor=actor,
            summary="consumed at settlement",
        )

    def halt_all(self, *, active: bool, actor: str) -> None:
        """The panic button: stop settlement for everyone."""
        self._kill_switch.set_global(active)
        self._log_control(
            actor=actor, summary=f"global kill switch {'engaged' if active else 'released'}"
        )

    def halt_principal(self, principal: str, *, active: bool, actor: str) -> None:
        """Stop settlement for one buyer."""
        self._kill_switch.set_for_principal(principal, active)
        self._log_control(
            actor=actor,
            summary=f"kill switch for {principal} "
            f"{'engaged' if active else 'released'}",
        )

    def is_halted(self, principal: str) -> bool:
        return self._kill_switch.is_active(principal)

    def get(self, mandate_id: str) -> MandateRecord | None:
        return self._store.get(mandate_id)

    def nonce_owner(self, nonce: str) -> str | None:
        return self._store.nonce_owner(nonce)

    def list_for_principal(self, principal: str) -> list[MandateRecord]:
        return self._store.list_by_principal(principal)

    def history(self, mandate_id: str) -> list[AuditEntry]:
        """Everything that has happened to one mandate, oldest first."""
        return self._audit.list_for_mandate(mandate_id)

    def record_verdict(self, verdict: Verdict) -> None:
        """Put a Verifier decision into the audit trail.

        The Verifier cannot do this itself — it has no side effects — so
        whoever called it hands the verdict here. Rejections are recorded just
        as carefully as approvals; a FAIL nobody can point to afterwards is not
        an auditable system.
        """
        reasons = ", ".join(verdict.reason_codes) or "all checks satisfied"
        self._append_audit(
            mandate_id=verdict.mandate_id,
            event=AuditEventType.VERDICT_ISSUED,
            occurred_at=verdict.evaluated_at,
            actor="verifier",
            summary=f"{verdict.decision}: {reasons}",
            verdict=verdict,
        )

    def record_candidate_selected(
        self, mandate_id: str, *, actor: str, summary: str
    ) -> None:
        """Record which product an agent chose, before anything has judged it.

        Deliberately prose and not a structured candidate: everything in a
        candidate is attacker-controlled, it is already carried faithfully on
        the `Charge`, and a second structured copy on the audit trail would be
        one more place to keep the untrusted values in step.
        """
        self._append_audit(
            mandate_id=mandate_id,
            event=AuditEventType.CANDIDATE_SELECTED,
            occurred_at=self._clock.now(),
            actor=actor,
            summary=summary,
        )

    def record_evaluation(self, mandate_id: str, *, evaluation: EvaluatorOutput) -> None:
        """Record what the Evaluator concluded, score and reasons intact.

        The actor is the evaluator id from the output itself, so the trail names
        whichever evaluator actually ran -- the rules one when the model was
        unreachable, the hybrid one when it was not.
        """
        reasons = "; ".join(evaluation.reasons) or "no reasons given"
        self._append_audit(
            mandate_id=mandate_id,
            event=AuditEventType.EVALUATION_COMPLETE,
            occurred_at=self._clock.now(),
            actor=evaluation.evaluator_id,
            summary=clip_text(f"risk {evaluation.risk_score}/10: {reasons}", 500),
            evaluation=evaluation,
        )

    def _register_onchain(
        self, record: MandateRecord, agent_address: str | None
    ) -> str | None:
        """Put the mandate on the ledger, if this deployment has one.

        The contract records the agent as an *address*, but `Mandate.agent_id`
        is a name and the Agent Registry is the thing that maps one to the
        other. This service does not consult the registry — that would make the
        one service allowed to sign also depend on another — so the caller
        passes the address it already looked up. Falling back to the principal
        keeps the contract's non-zero requirement satisfied when nobody did,
        and the offchain audit log carries the agent id either way.
        """
        if self._registrar is None:
            return None
        mandate = record.signed.mandate
        return self._registrar.register(
            mandate_id=mandate.mandate_id,
            principal=mandate.principal,
            agent_address=agent_address or mandate.principal,
            cap_minor_units=mandate.max_amount.minor_units,
            expires_at=mandate.expires_at,
            digest=record.signed.digest,
        )

    def _sign(self, mandate: Mandate) -> SignedMandate:
        digest = mandate_digest(mandate, self._domain)
        return SignedMandate(
            mandate=mandate,
            digest=digest,
            signature=self._signer.sign(to_bytes(digest)),
        )

    def _transition(
        self,
        mandate_id: str,
        *,
        to: MandateStatus,
        event: AuditEventType,
        actor: str,
        summary: str,
    ) -> MandateRecord:
        record = self._require(mandate_id)
        if record.status not in _LIVE_STATUSES:
            raise MandateStatusConflict(
                f"mandate {mandate_id} is already {record.status}"
            )
        updated = record.evolve(status=to, at=self._clock.now())
        self._store.replace(updated, expected_status=record.status)
        self._log(updated, event, actor=actor, summary=summary)
        return updated

    def _require(self, mandate_id: str) -> MandateRecord:
        record = self._store.get(mandate_id)
        if record is None:
            raise MandateNotFound(mandate_id)
        return record

    def _append_audit(
        self,
        *,
        mandate_id: str,
        event: AuditEventType,
        occurred_at: Timestamp,
        actor: str,
        summary: str,
        verdict: Verdict | None = None,
        evaluation: EvaluatorOutput | None = None,
    ) -> None:
        """The one place an audit entry is constructed, and the one place it is logged.

        Logging here rather than at each call site is what makes the two records
        agree by construction: an event cannot reach the audit trail without
        also reaching the log stream. CLAUDE.md asks for both -- DynamoDB for the
        dashboard, CloudWatch for operations -- and two independently maintained
        lists of what to record is how they drift.

        The audit trail remains the durable record. This is for the operator
        watching a terminal, and for a log stream that outlives an in-memory
        store.
        """
        entry = AuditEntry(
            event_id=new_hex32(),
            mandate_id=mandate_id,
            event_type=event,
            occurred_at=occurred_at,
            actor=actor,
            summary=summary,
            verdict=verdict,
            evaluation=evaluation,
        )
        self._audit.record(entry)
        _log_entry(entry)

    def _log(
        self,
        record: MandateRecord,
        event: AuditEventType,
        *,
        actor: str,
        summary: str,
    ) -> None:
        self._append_audit(
            mandate_id=record.mandate_id,
            event=event,
            occurred_at=record.updated_at,
            actor=actor,
            summary=summary,
        )

    def _log_control(self, *, actor: str, summary: str) -> None:
        # The kill switch is not scoped to a mandate, but the audit log is keyed
        # by one. A zero id keeps every control action in the same trail.
        self._append_audit(
            mandate_id=ZERO_HASH,
            event=AuditEventType.KILL_SWITCH_SET,
            occurred_at=self._clock.now(),
            actor=actor,
            summary=summary,
        )


def _log_entry(entry: AuditEntry) -> None:
    """Emit one audit entry as a structured log line.

    The message stays short and the detail goes in `extra`, so a log stream can
    be filtered on `decision` or `mandate_id` without parsing prose. A FAIL and
    a revocation are raised to WARNING because they are what an operator scans
    for, and at INFO they would be buried by the events around them.
    """
    decision = entry.verdict.decision.value if entry.verdict else None
    notable = entry.event_type in _NOTABLE_EVENTS or decision == Decision.FAIL.value
    logger.log(
        logging.WARNING if notable else logging.INFO,
        "%s %s",
        entry.event_type.value,
        entry.summary,
        extra={
            "event_type": entry.event_type.value,
            "mandate_id": entry.mandate_id,
            "actor": entry.actor,
            "decision": decision,
            "reason_codes": [code.value for code in entry.verdict.reason_codes]
            if entry.verdict
            else None,
            "failed_deterministically": entry.verdict.failed_deterministically
            if entry.verdict
            else None,
            "risk_score": entry.evaluation.risk_score if entry.evaluation else None,
        },
    )
