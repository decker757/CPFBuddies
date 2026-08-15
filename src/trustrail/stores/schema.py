"""DynamoDB table definitions, as code.

Kept next to the stores that use them so the two cannot drift, and callable from
a deploy script or a test using moto. Point-in-time recovery is on for every
table: this is the audit trail for money movement.
"""

from __future__ import annotations

from typing import Any

import boto3

from trustrail.stores.dynamo import (
    AUDIT_TABLE,
    CONTROL_TABLE,
    MANDATES_TABLE,
    PRINCIPAL_INDEX,
    REVIEW_HOLDS_TABLE,
)

_STRING = "S"
_PAY_PER_REQUEST = "PAY_PER_REQUEST"


def create_tables(resource: Any = None, *, with_backups: bool = True) -> None:
    """Create every table this package needs, if it does not already exist."""
    dynamodb = resource or boto3.resource("dynamodb")
    for definition in _table_definitions():
        _create(dynamodb, definition)
    _enable_ttl(dynamodb, REVIEW_HOLDS_TABLE, attribute="ttl")
    if with_backups:
        for name in _TABLE_NAMES:
            _enable_pitr(dynamodb, name)


_TABLE_NAMES = (MANDATES_TABLE, AUDIT_TABLE, CONTROL_TABLE, REVIEW_HOLDS_TABLE)


def _table_definitions() -> list[dict[str, Any]]:
    return [
        {
            # One table holds both mandates (`mandate#<id>`) and their nonce
            # claims (`nonce#<nonce>`), so the two can be written in a single
            # transaction. Only mandate items carry `principal`, which makes the
            # index below sparse — nonce claims never appear in it.
            "TableName": MANDATES_TABLE,
            "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "pk", "AttributeType": _STRING},
                {"AttributeName": "principal", "AttributeType": _STRING},
                {"AttributeName": "created_at", "AttributeType": _STRING},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": PRINCIPAL_INDEX,
                    "KeySchema": [
                        {"AttributeName": "principal", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        {
            "TableName": AUDIT_TABLE,
            "KeySchema": [
                {"AttributeName": "mandate_id", "KeyType": "HASH"},
                {"AttributeName": "occurred_at_event", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "mandate_id", "AttributeType": _STRING},
                {"AttributeName": "occurred_at_event", "AttributeType": _STRING},
            ],
        },
        {
            "TableName": CONTROL_TABLE,
            "KeySchema": [{"AttributeName": "scope", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "scope", "AttributeType": _STRING}
            ],
        },
        {
            "TableName": REVIEW_HOLDS_TABLE,
            "KeySchema": [{"AttributeName": "charge_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "charge_id", "AttributeType": _STRING}
            ],
        },
    ]


def _create(dynamodb: Any, definition: dict[str, Any]) -> None:
    client = dynamodb.meta.client
    try:
        client.create_table(BillingMode=_PAY_PER_REQUEST, **definition)
    except client.exceptions.ResourceInUseException:
        return  # already there; creating tables is meant to be re-runnable
    client.get_waiter("table_exists").wait(TableName=definition["TableName"])


def _enable_ttl(dynamodb: Any, table_name: str, *, attribute: str) -> None:
    dynamodb.meta.client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": attribute},
    )


def _enable_pitr(dynamodb: Any, table_name: str) -> None:
    dynamodb.meta.client.update_continuous_backups(
        TableName=table_name,
        PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
    )
