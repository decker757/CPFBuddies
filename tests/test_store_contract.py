"""One suite, run against both store implementations.

The in-memory stores are not a convenience — the tests and the offline demo run
on them, so anything they get wrong is something production would get wrong
silently. Running the same suite against DynamoDB is how the two are kept
honest, particularly the conditional writes that one-time consumption depends
on.

The DynamoDB half runs against moto and needs no AWS account.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest

from trustrail.contracts.scenarios import DEMO_NOW, ScenarioBuilder
from trustrail.errors import (
    MandateAlreadyExists,
    MandateNotFound,
    MandateStatusConflict,
    NonceAlreadyClaimed,
)
from trustrail.models.audit import AuditEntry, AuditEventType
from trustrail.models.mandate import MandateRecord, MandateStatus
from trustrail.models.review import ReviewHold, ReviewOutcome
from trustrail.stores.memory import (
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
    InMemoryReviewHoldStore,
)

PRINCIPAL = "0x" + "11" * 20
OTHER_PRINCIPAL = "0x" + "22" * 20


@pytest.fixture
def stores(request: pytest.FixtureRequest) -> Iterator[dict[str, Any]]:
    """Every store, from whichever backend this parametrisation asked for."""
    if request.param == "memory":
        yield {
            "mandates": InMemoryMandateStore(),
            "kill_switch": InMemoryKillSwitchStore(),
            "audit": InMemoryAuditLog(),
            "holds": InMemoryReviewHoldStore(),
        }
        return
    yield from _dynamo_stores()


def _dynamo_stores() -> Iterator[dict[str, Any]]:
    moto = pytest.importorskip("moto", reason="moto is needed for the DynamoDB half")
    import boto3

    from trustrail.stores.dynamo import (
        DynamoAuditLog,
        DynamoKillSwitchStore,
        DynamoMandateStore,
        DynamoReviewHoldStore,
    )
    from trustrail.stores.schema import create_tables

    with moto.mock_aws():
        resource = boto3.resource(
            "dynamodb",
            region_name="ap-southeast-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_tables(resource, with_backups=False)
        yield {
            "mandates": DynamoMandateStore(resource=resource),
            "kill_switch": DynamoKillSwitchStore(resource=resource),
            "audit": DynamoAuditLog(resource=resource),
            "holds": DynamoReviewHoldStore(resource=resource),
        }


pytestmark = pytest.mark.parametrize(
    "stores", ["memory", "dynamo"], indirect=True, ids=["memory", "dynamo"]
)


def _record(
    build: ScenarioBuilder,
    *,
    mandate_id: str = "0x" + "a1" * 32,
    nonce: str = "0x" + "b1" * 32,
    principal: str = PRINCIPAL,
    status: MandateStatus = MandateStatus.MINTED,
    created_at: datetime = DEMO_NOW,
) -> MandateRecord:
    mandate = build.mandate(
        mandate_id=mandate_id, nonce=nonce, principal=principal
    )
    return MandateRecord(
        signed=build.sign_mandate(mandate),
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


# --- mandates --------------------------------------------------------------


def test_a_saved_mandate_comes_back_unchanged(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    record = _record(build)

    stores["mandates"].save_new(record)

    assert stores["mandates"].get(record.mandate_id) == record


def test_an_unknown_mandate_is_none_not_an_error(stores: dict[str, Any]) -> None:
    assert stores["mandates"].get("0x" + "ff" * 32) is None


def test_a_mandate_id_cannot_be_reused(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    stores["mandates"].save_new(_record(build))

    with pytest.raises(MandateAlreadyExists):
        stores["mandates"].save_new(_record(build, nonce="0x" + "c1" * 32))


def test_a_nonce_cannot_be_reused(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    """The claim on the nonce is written with the mandate, in one transaction."""
    stores["mandates"].save_new(_record(build))

    with pytest.raises(NonceAlreadyClaimed):
        stores["mandates"].save_new(_record(build, mandate_id="0x" + "a2" * 32))


def test_the_nonce_index_names_its_owner(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    record = _record(build)
    stores["mandates"].save_new(record)

    assert stores["mandates"].nonce_owner(record.signed.mandate.nonce) == (
        record.mandate_id
    )
    assert stores["mandates"].nonce_owner("0x" + "ee" * 32) is None


def test_a_conditional_write_succeeds_when_the_status_still_matches(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    record = _record(build)
    stores["mandates"].save_new(record)
    consumed = record.evolve(status=MandateStatus.CONSUMED, at=DEMO_NOW)

    stores["mandates"].replace(consumed, expected_status=MandateStatus.MINTED)

    assert stores["mandates"].get(record.mandate_id).status is MandateStatus.CONSUMED


def test_a_conditional_write_fails_once_the_status_has_moved(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    """This is one-time consumption. If it regresses, mandates become reusable."""
    record = _record(build)
    stores["mandates"].save_new(record)
    consumed = record.evolve(status=MandateStatus.CONSUMED, at=DEMO_NOW)
    stores["mandates"].replace(consumed, expected_status=MandateStatus.MINTED)

    with pytest.raises(MandateStatusConflict):
        stores["mandates"].replace(consumed, expected_status=MandateStatus.MINTED)


def test_replacing_an_unknown_mandate_is_reported_as_missing(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    with pytest.raises(MandateNotFound):
        stores["mandates"].replace(
            _record(build), expected_status=MandateStatus.MINTED
        )


def test_mandates_are_listed_per_principal_newest_first(
    stores: dict[str, Any], build: ScenarioBuilder
) -> None:
    older = _record(build, mandate_id="0x" + "a1" * 32, nonce="0x" + "b1" * 32)
    newer = _record(
        build,
        mandate_id="0x" + "a2" * 32,
        nonce="0x" + "b2" * 32,
        created_at=DEMO_NOW + timedelta(minutes=5),
    )
    other_buyer = _record(
        build,
        mandate_id="0x" + "a3" * 32,
        nonce="0x" + "b3" * 32,
        principal=OTHER_PRINCIPAL,
    )
    for record in (older, newer, other_buyer):
        stores["mandates"].save_new(record)

    listed = stores["mandates"].list_by_principal(PRINCIPAL)

    assert [r.mandate_id for r in listed] == [newer.mandate_id, older.mandate_id]


# --- kill switch -----------------------------------------------------------


def test_the_global_switch_halts_everyone(stores: dict[str, Any]) -> None:
    stores["kill_switch"].set_global(True)

    assert stores["kill_switch"].is_active(PRINCIPAL)
    assert stores["kill_switch"].is_active(OTHER_PRINCIPAL)


def test_a_principal_switch_is_scoped_to_that_principal(
    stores: dict[str, Any],
) -> None:
    stores["kill_switch"].set_for_principal(PRINCIPAL, True)

    assert stores["kill_switch"].is_active(PRINCIPAL)
    assert not stores["kill_switch"].is_active(OTHER_PRINCIPAL)


def test_switches_can_be_released(stores: dict[str, Any]) -> None:
    stores["kill_switch"].set_global(True)
    stores["kill_switch"].set_global(False)

    assert not stores["kill_switch"].is_active(PRINCIPAL)


def test_nothing_is_halted_by_default(stores: dict[str, Any]) -> None:
    assert not stores["kill_switch"].is_active(PRINCIPAL)


# --- audit log -------------------------------------------------------------


def _entry(mandate_id: str, *, seq: int) -> AuditEntry:
    return AuditEntry(
        event_id="0x" + f"{seq:02x}" * 32,
        mandate_id=mandate_id,
        event_type=AuditEventType.MANDATE_MINTED,
        occurred_at=DEMO_NOW + timedelta(seconds=seq),
        actor="test",
        summary=f"event {seq}",
    )


def test_audit_entries_come_back_in_the_order_they_happened(
    stores: dict[str, Any],
) -> None:
    mandate_id = "0x" + "a1" * 32
    for seq in (1, 2, 3):
        stores["audit"].record(_entry(mandate_id, seq=seq))

    entries = stores["audit"].list_for_mandate(mandate_id)

    assert [e.summary for e in entries] == ["event 1", "event 2", "event 3"]


def test_audit_entries_are_scoped_to_their_mandate(stores: dict[str, Any]) -> None:
    stores["audit"].record(_entry("0x" + "a1" * 32, seq=1))
    stores["audit"].record(_entry("0x" + "a2" * 32, seq=2))

    assert len(stores["audit"].list_for_mandate("0x" + "a1" * 32)) == 1


# --- review holds ----------------------------------------------------------


@pytest.fixture
def held_verdict(build: ScenarioBuilder, verifier: Any) -> Any:
    return verifier.verify(build.request())


@pytest.fixture
def hold(build: ScenarioBuilder, held_verdict: Any) -> ReviewHold:
    return build.hold(held_verdict, deadline=DEMO_NOW + timedelta(minutes=5))


def test_a_hold_round_trips(stores: dict[str, Any], hold: ReviewHold) -> None:
    stores["holds"].put(hold)

    assert stores["holds"].get(hold.charge_id) == hold


def test_pending_holds_are_listed_while_they_are_still_waiting(
    stores: dict[str, Any], hold: ReviewHold
) -> None:
    stores["holds"].put(hold)

    assert stores["holds"].list_pending(DEMO_NOW) == [hold]


def test_a_hold_past_its_deadline_is_no_longer_pending(
    stores: dict[str, Any], hold: ReviewHold
) -> None:
    """No sweeper, no indefinite queue: the deadline is enforced on read."""
    stores["holds"].put(hold)

    assert stores["holds"].list_pending(DEMO_NOW + timedelta(minutes=6)) == []


def test_a_resolved_hold_is_no_longer_pending(
    stores: dict[str, Any], hold: ReviewHold
) -> None:
    stores["holds"].put(hold)

    stores["holds"].put(hold.resolve(outcome=ReviewOutcome.KILLED, by="ernest"))

    assert stores["holds"].list_pending(DEMO_NOW) == []
