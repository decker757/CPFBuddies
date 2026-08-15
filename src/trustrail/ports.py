"""The seams between this package and the outside world.

Five protocols, one file, so the boundaries are visible at a glance. Every one
of them has an in-memory implementation used by the tests and the offline demo,
and an AWS implementation used in deployment. Nothing in `mandate/` or
`verifier/` imports boto3.

The Verifier depends on none of these. It is handed data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trustrail.models.audit import AuditEntry
from trustrail.models.mandate import MandateRecord, MandateStatus
from trustrail.models.review import ReviewHold


class Clock(Protocol):
    """Current time, so expiry can be tested without sleeping."""

    def now(self) -> datetime: ...


class Signer(Protocol):
    """Produces secp256k1 signatures for a key we may never see.

    `KmsSigner` never has the key material; `LocalSigner` does. Everything
    downstream only needs the address, which is what verification compares
    against.
    """

    @property
    def address(self) -> str:
        """The lowercase address whose signatures this signer produces."""
        ...

    def sign(self, digest: bytes) -> str:
        """Sign a 32-byte digest as `0x` + r || s || v."""
        ...


class MandateStore(Protocol):
    """Persistence for mandates, with the conditional writes that make
    one-time consumption real."""

    def save_new(self, record: MandateRecord) -> None:
        """Persist a freshly minted mandate and claim its nonce.

        Raises `MandateAlreadyExists` or `NonceAlreadyClaimed` rather than
        overwriting — both would mean uniqueness has already broken.
        """
        ...

    def get(self, mandate_id: str) -> MandateRecord | None: ...

    def nonce_owner(self, nonce: str) -> str | None:
        """The mandate id that first claimed `nonce`, if any."""
        ...

    def replace(
        self, record: MandateRecord, *, expected_status: MandateStatus
    ) -> None:
        """Write `record` only if its stored status is still `expected_status`.

        Every lifecycle transition — bind, revoke, consume — goes through this
        one primitive, so each of them is a compare-and-set rather than a
        read-then-write that a concurrent worker could interleave with.

        Raises `MandateStatusConflict` when the stored status has moved.
        """
        ...

    def list_by_principal(self, principal: str) -> list[MandateRecord]:
        """Mandates for one buyer, newest first. Backs the approval UI."""
        ...


class KillSwitchStore(Protocol):
    """The emergency stop, global or per buyer."""

    def is_active(self, principal: str) -> bool:
        """True if the global switch is on, or this principal's switch is."""
        ...

    def set_global(self, active: bool) -> None: ...

    def set_for_principal(self, principal: str, active: bool) -> None: ...


class AuditLog(Protocol):
    """Append-only record of every state change and every verdict."""

    def record(self, entry: AuditEntry) -> None: ...

    def list_for_mandate(self, mandate_id: str) -> list[AuditEntry]:
        """Entries for one mandate, oldest first."""
        ...


class ReviewHoldStore(Protocol):
    """Charges paused for a human, each with a deadline it cannot outlive."""

    def put(self, hold: ReviewHold) -> None: ...

    def get(self, charge_id: str) -> ReviewHold | None: ...

    def list_pending(self, now: datetime) -> list[ReviewHold]:
        """Holds still awaiting a human and not yet past their deadline.

        Anything past its deadline is simply not returned. There is no sweeper
        to fall behind and no pending queue to grow without bound — in DynamoDB
        the TTL attribute eventually removes the row as well.
        """
        ...
