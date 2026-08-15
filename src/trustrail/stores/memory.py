"""In-memory stores: the default for tests and the offline demo path.

These are not toys. They implement the same compare-and-set semantics as the
DynamoDB versions, under a lock, so a test that races two workers onto one
mandate exercises the same guarantee production relies on. If one-time
consumption breaks, it breaks here too.
"""

from __future__ import annotations

import threading
from datetime import datetime

from trustrail.errors import (
    MandateAlreadyExists,
    MandateNotFound,
    MandateStatusConflict,
    NonceAlreadyClaimed,
)
from trustrail.models.audit import AuditEntry
from trustrail.models.mandate import MandateRecord, MandateStatus
from trustrail.models.registry import AgentRecord, MerchantRecord
from trustrail.models.review import ReviewHold, ReviewOutcome

_GLOBAL_SCOPE = "global"


class InMemoryMandateStore:
    """Mandates, their nonces, and the conditional writes over both."""

    def __init__(self) -> None:
        self._records: dict[str, MandateRecord] = {}
        self._nonce_owners: dict[str, str] = {}
        self._lock = threading.Lock()

    def save_new(self, record: MandateRecord) -> None:
        nonce = record.signed.mandate.nonce
        with self._lock:
            if record.mandate_id in self._records:
                raise MandateAlreadyExists(record.mandate_id)
            owner = self._nonce_owners.get(nonce)
            if owner is not None:
                raise NonceAlreadyClaimed(f"nonce already held by {owner}")
            self._records[record.mandate_id] = record
            self._nonce_owners[nonce] = record.mandate_id

    def get(self, mandate_id: str) -> MandateRecord | None:
        with self._lock:
            return self._records.get(mandate_id)

    def nonce_owner(self, nonce: str) -> str | None:
        with self._lock:
            return self._nonce_owners.get(nonce)

    def replace(
        self, record: MandateRecord, *, expected_status: MandateStatus
    ) -> None:
        with self._lock:
            current = self._records.get(record.mandate_id)
            if current is None:
                raise MandateNotFound(record.mandate_id)
            if current.status is not expected_status:
                raise MandateStatusConflict(
                    f"expected {expected_status}, found {current.status}"
                )
            self._records[record.mandate_id] = record

    def list_by_principal(self, principal: str) -> list[MandateRecord]:
        with self._lock:
            owned = [r for r in self._records.values() if r.principal == principal]
        return sorted(owned, key=lambda record: record.created_at, reverse=True)


class InMemoryKillSwitchStore:
    """The global switch and the per-principal switches. Either one halts."""

    def __init__(self) -> None:
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def is_active(self, principal: str) -> bool:
        with self._lock:
            return _GLOBAL_SCOPE in self._active or principal.lower() in self._active

    def set_global(self, active: bool) -> None:
        self._set(_GLOBAL_SCOPE, active)

    def set_for_principal(self, principal: str, active: bool) -> None:
        self._set(principal.lower(), active)

    def _set(self, scope: str, active: bool) -> None:
        with self._lock:
            if active:
                self._active.add(scope)
            else:
                self._active.discard(scope)


class InMemoryAuditLog:
    """Append-only. Entries are never updated or removed, only added."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def record(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def list_for_mandate(self, mandate_id: str) -> list[AuditEntry]:
        with self._lock:
            return [e for e in self._entries if e.mandate_id == mandate_id]

    def all_entries(self) -> list[AuditEntry]:
        """Every entry, oldest first. Backs the demo's decision dashboard."""
        with self._lock:
            return list(self._entries)


class InMemoryMerchantDirectory:
    """Registered merchant platforms, keyed by merchant id.

    In memory rather than in DynamoDB because these records are seeded by the
    Onboarding Orchestrator at boot, not accumulated at runtime — CLAUDE.md
    says the registries "can be seeded by script for the demo". The port is a
    Protocol, so a persistent implementation drops in without the orchestrator
    noticing.
    """

    def __init__(self) -> None:
        self._records: dict[str, MerchantRecord] = {}
        self._lock = threading.Lock()

    def get(self, merchant_id: str) -> MerchantRecord | None:
        with self._lock:
            return self._records.get(merchant_id)

    def put(self, record: MerchantRecord) -> None:
        with self._lock:
            self._records[record.merchant_id] = record

    def list_all(self) -> list[MerchantRecord]:
        with self._lock:
            records = list(self._records.values())
        return sorted(records, key=lambda record: record.merchant_id)


class InMemoryAgentDirectory:
    """Internal agent identities, keyed by agent id. Seeded, like merchants."""

    def __init__(self) -> None:
        self._records: dict[str, AgentRecord] = {}
        self._lock = threading.Lock()

    def get(self, agent_id: str) -> AgentRecord | None:
        with self._lock:
            return self._records.get(agent_id)

    def put(self, record: AgentRecord) -> None:
        with self._lock:
            self._records[record.agent_id] = record

    def list_all(self) -> list[AgentRecord]:
        with self._lock:
            records = list(self._records.values())
        return sorted(records, key=lambda record: record.agent_id)


class InMemoryReviewHoldStore:
    """Held charges, filtered by deadline on read rather than swept."""

    def __init__(self) -> None:
        self._holds: dict[str, ReviewHold] = {}
        self._lock = threading.Lock()

    def put(self, hold: ReviewHold) -> None:
        with self._lock:
            self._holds[hold.charge_id] = hold

    def get(self, charge_id: str) -> ReviewHold | None:
        with self._lock:
            return self._holds.get(charge_id)

    def list_pending(self, now: datetime) -> list[ReviewHold]:
        with self._lock:
            holds = list(self._holds.values())
        return [
            hold
            for hold in holds
            if hold.outcome is ReviewOutcome.PENDING and hold.deadline > now
        ]
