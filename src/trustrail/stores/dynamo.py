"""DynamoDB implementations of the same ports the in-memory stores implement.

Three design choices worth stating, because each one is doing real work:

**Models are stored as a JSON string, with the queryable fields lifted out
alongside.** DynamoDB has no float type and its `Decimal` handling is a reliable
source of subtle money bugs. Storing `model_dump_json()` means what comes back
out is byte-identical to what went in, and the fields we actually query on —
status, principal, created_at — are duplicated as plain attributes.

**Mandate creation is a transaction over two items.** The mandate and a claim on
its nonce are written together, each conditional on not already existing. A
partial write here would mean a nonce claimed with no mandate behind it, or
worse, two mandates sharing a nonce.

**Every update is conditional on the status we read.** That is what makes
one-time consumption survive two settlement workers racing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from trustrail.errors import (
    MandateAlreadyExists,
    MandateNotFound,
    MandateStatusConflict,
    NonceAlreadyClaimed,
)
from trustrail.models.audit import AuditEntry
from trustrail.models.mandate import MandateRecord, MandateStatus
from trustrail.models.review import ReviewHold, ReviewOutcome

MANDATES_TABLE = "trustrail-mandates"
AUDIT_TABLE = "trustrail-audit-log"
CONTROL_TABLE = "trustrail-control"
REVIEW_HOLDS_TABLE = "trustrail-review-holds"

PRINCIPAL_INDEX = "principal-created-at-index"

_GLOBAL_SCOPE = "global"
_MANDATE_PREFIX = "mandate#"
_NONCE_PREFIX = "nonce#"

#: DynamoDB spells a failed condition two different ways. A single-item write
#: raises an error whose code carries the `Exception` suffix; a transaction
#: succeeds at the API level and reports per-item codes without it. Conflating
#: the two silently mislabels every failure.
_CONDITION_FAILED_REQUEST = "ConditionalCheckFailedException"
_CONDITION_FAILED_ITEM = "ConditionalCheckFailed"
_TRANSACTION_CANCELLED = "TransactionCanceledException"


class DynamoMandateStore:
    """Mandates and their nonce claims, in one table."""

    def __init__(self, table_name: str = MANDATES_TABLE, *, resource: Any = None):
        self._dynamodb = resource or boto3.resource("dynamodb")
        self._table = self._dynamodb.Table(table_name)
        self._table_name = table_name

    # This store needs the resource itself, not just the table, because
    # `save_new` is a transaction across two items and that is a client call.

    def save_new(self, record: MandateRecord) -> None:
        # Order matters: `_explain_failed_creation` reads the per-item results
        # positionally, because that is the only way DynamoDB tells you which
        # half of the transaction failed.
        items = [_mandate_item(record), _nonce_item(record)]
        try:
            self._dynamodb.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": item,
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    }
                    for item in items
                ]
            )
        except ClientError as exc:
            raise _explain_failed_creation(exc, record) from exc

    def get(self, mandate_id: str) -> MandateRecord | None:
        item = self._table.get_item(Key={"pk": _MANDATE_PREFIX + mandate_id}).get("Item")
        return MandateRecord.model_validate_json(item["record"]) if item else None

    def nonce_owner(self, nonce: str) -> str | None:
        item = self._table.get_item(Key={"pk": _NONCE_PREFIX + nonce}).get("Item")
        return item["mandate_id"] if item else None

    def replace(
        self, record: MandateRecord, *, expected_status: MandateStatus
    ) -> None:
        try:
            self._table.put_item(
                Item=_mandate_item(record),
                ConditionExpression=Attr("status").eq(expected_status.value),
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != _CONDITION_FAILED_REQUEST:
                raise
            # The condition covers both "someone else moved it" and "it was
            # never there", so distinguish them for a useful error message.
            if self.get(record.mandate_id) is None:
                raise MandateNotFound(record.mandate_id) from exc
            raise MandateStatusConflict(
                f"mandate {record.mandate_id} is no longer {expected_status}"
            ) from exc

    def list_by_principal(self, principal: str) -> list[MandateRecord]:
        response = self._table.query(
            IndexName=PRINCIPAL_INDEX,
            KeyConditionExpression=Key("principal").eq(principal),
            ScanIndexForward=False,
        )
        return [MandateRecord.model_validate_json(i["record"]) for i in response["Items"]]


class DynamoKillSwitchStore:
    """One tiny table, two kinds of row: the global halt and per-principal halts."""

    def __init__(self, table_name: str = CONTROL_TABLE, *, resource: Any = None):
        self._table = _table(table_name, resource)

    def is_active(self, principal: str) -> bool:
        return self._read(_GLOBAL_SCOPE) or self._read(principal.lower())

    def set_global(self, active: bool) -> None:
        self._write(_GLOBAL_SCOPE, active)

    def set_for_principal(self, principal: str, active: bool) -> None:
        self._write(principal.lower(), active)

    def _read(self, scope: str) -> bool:
        item = self._table.get_item(Key={"scope": scope}).get("Item")
        return bool(item and item["active"])

    def _write(self, scope: str, active: bool) -> None:
        self._table.put_item(Item={"scope": scope, "active": active})


class DynamoAuditLog:
    """Append-only. Sorted by time within a mandate, so reads are already ordered."""

    def __init__(self, table_name: str = AUDIT_TABLE, *, resource: Any = None):
        self._table = _table(table_name, resource)

    def record(self, entry: AuditEntry) -> None:
        self._table.put_item(
            Item={
                "mandate_id": entry.mandate_id,
                # Time first so the range key sorts chronologically; the event
                # id only breaks ties between entries in the same microsecond.
                "occurred_at_event": f"{entry.occurred_at.isoformat()}#{entry.event_id}",
                "event_type": entry.event_type.value,
                "entry": entry.model_dump_json(),
            }
        )

    def list_for_mandate(self, mandate_id: str) -> list[AuditEntry]:
        response = self._table.query(
            KeyConditionExpression=Key("mandate_id").eq(mandate_id)
        )
        return [AuditEntry.model_validate_json(i["entry"]) for i in response["Items"]]


class DynamoReviewHoldStore:
    """Held charges, with DynamoDB TTL doing the cleanup.

    `ttl` is set from the hold's deadline, so an unanswered REVIEW eventually
    disappears on its own. Reads still filter by deadline rather than trusting
    TTL timing — DynamoDB deletes expired items within about 48 hours, which is
    fine for storage and useless as a correctness guarantee.
    """

    def __init__(self, table_name: str = REVIEW_HOLDS_TABLE, *, resource: Any = None):
        self._table = _table(table_name, resource)

    def put(self, hold: ReviewHold) -> None:
        self._table.put_item(
            Item={
                "charge_id": hold.charge_id,
                "outcome": hold.outcome.value,
                "deadline": hold.deadline.isoformat(),
                "ttl": int(hold.deadline.timestamp()),
                "hold": hold.model_dump_json(),
            }
        )

    def get(self, charge_id: str) -> ReviewHold | None:
        item = self._table.get_item(Key={"charge_id": charge_id}).get("Item")
        return ReviewHold.model_validate_json(item["hold"]) if item else None

    def list_pending(self, now: datetime) -> list[ReviewHold]:
        response = self._table.scan(
            FilterExpression=Attr("outcome").eq(ReviewOutcome.PENDING.value)
            & Attr("deadline").gt(now.isoformat())
        )
        return [ReviewHold.model_validate_json(i["hold"]) for i in response["Items"]]


def _table(table_name: str, resource: Any) -> Any:
    """Resolve a table, defaulting to the ambient boto3 session.

    Taking the resource as an argument is what lets the whole DynamoDB suite run
    against moto without patching anything.
    """
    return (resource or boto3.resource("dynamodb")).Table(table_name)


def _mandate_item(record: MandateRecord) -> dict[str, Any]:
    return {
        "pk": _MANDATE_PREFIX + record.mandate_id,
        "principal": record.principal,
        "created_at": record.created_at.isoformat(),
        "status": record.status.value,
        "record": record.model_dump_json(),
    }


def _nonce_item(record: MandateRecord) -> dict[str, Any]:
    return {
        "pk": _NONCE_PREFIX + record.signed.mandate.nonce,
        "mandate_id": record.mandate_id,
    }


#: Positions in the `save_new` transaction, and what a failure at each means.
_MANDATE_ITEM, _NONCE_ITEM = 0, 1


def _explain_failed_creation(exc: ClientError, record: MandateRecord) -> Exception:
    """Turn a cancelled transaction into the specific uniqueness that broke.

    The two failures mean very different things — a duplicate mandate id is a
    retry, a duplicate nonce means uniqueness itself has broken — so anything
    that is neither is returned untouched rather than being labelled as one of
    them.
    """
    if exc.response["Error"]["Code"] != _TRANSACTION_CANCELLED:
        return exc
    reasons = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
    if _failed(reasons, _MANDATE_ITEM):
        return MandateAlreadyExists(record.mandate_id)
    if _failed(reasons, _NONCE_ITEM):
        return NonceAlreadyClaimed(record.signed.mandate.nonce)
    return exc


def _failed(reasons: list[str | None], index: int) -> bool:
    return len(reasons) > index and reasons[index] == _CONDITION_FAILED_ITEM
