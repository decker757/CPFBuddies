"""The seams between this package and the outside world.

Seven protocols, one file, so the boundaries are visible at a glance. Each has
an in-memory implementation used by the tests and the offline demo; the ones
that hold state worth surviving a restart also have an AWS implementation.
Nothing in `mandate/` or `verifier/` imports boto3.

The Verifier depends on none of these. It is handed data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from trustrail.models.audit import AuditEntry
from trustrail.models.mandate import MandateRecord, MandateStatus
from trustrail.models.registry import AgentRecord, MerchantRecord
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

    def all_entries(self) -> list[AuditEntry]:
        """Every entry, oldest first. Backs the decision dashboard.

        On the port because the dashboard is a cross-mandate view and there was
        no way to ask for one: `list_for_mandate` needs an id, and the feed's
        whole job is to show purchases nobody has asked about yet.

        Oldest first, in a **stable total order** -- the same entries must come
        back in the same order on every read. That is what a feed cursor rests
        on: reorder the history between two reads and a client resuming from
        position N silently skips or repeats rows. Timestamps alone are not
        enough, because entries can share one: the Purchase Orchestrator records
        the candidate and the evaluation together, since workstream B hands it
        both at once. An implementation ordering by time needs a deterministic
        tiebreak.
        """
        ...


class MandateRegistrar(Protocol):
    """Puts a mandate on the ledger, and takes its authority away again.

    The chain is where enforcement actually lives: `MandateRegistry.spend`
    re-checks the cap, the merchant, the expiry and one-time consumption on its
    own, so a compromised backend cannot move money outside a mandate. None of
    that can happen for a mandate the contract has never heard of, which is why
    registration is part of minting rather than an afterthought at settlement.

    This is a Protocol so `trustrail.mandate` never imports web3. The chain
    implementation is `settlement.chain.registrar.ChainMandateRegistrar`; the
    offline demo and the tests pass nothing at all and stay entirely local.

    Whoever implements this holds REGISTRAR_ROLE, and that is deliberately
    *not* the settler's key: a compromised settlement worker can then still
    never register a mandate of its own invention.
    """

    def register(
        self,
        *,
        mandate_id: str,
        principal: str,
        agent_address: str,
        cap_minor_units: int,
        expires_at: datetime,
        digest: str,
    ) -> str | None:
        """Record a freshly minted mandate. Returns a transaction hash if it made one.

        The merchant is deliberately not a parameter. A mandate is minted before
        a product is chosen, so there is nothing to bind yet; the contract takes
        `address(0)` and binds on first spend.

        Raises if the mandate could not be registered. Minting something that
        can never settle is worse than failing loudly.
        """
        ...

    def revoke(self, mandate_id: str) -> str | None:
        """Withdraw a mandate's authority onchain. Returns a transaction hash."""
        ...


class MerchantDirectory(Protocol):
    """Registered merchant platforms, looked up by the id a charge names.

    `get` returning None is not an error condition to swallow: it means the
    counterparty is unregistered, which the Verifier turns into a deterministic
    FAIL. The caller passes the None straight through.
    """

    def get(self, merchant_id: str) -> MerchantRecord | None: ...

    def put(self, record: MerchantRecord) -> None: ...

    def list_all(self) -> list[MerchantRecord]:
        """Every registered platform. Backs `GET /merchants` for discovery."""
        ...


class AgentDirectory(Protocol):
    """Internal agents and the keys they sign their output with.

    This is what makes a compromised Browser Agent unable to forge itself a
    clean risk score: the Verifier checks the evaluator's signature against the
    address on file here, and an agent it has never heard of is a FAIL.
    """

    def get(self, agent_id: str) -> AgentRecord | None: ...

    def put(self, record: AgentRecord) -> None: ...

    def list_all(self) -> list[AgentRecord]: ...


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
