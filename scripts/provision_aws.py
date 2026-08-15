"""Create the AWS resources this rail runs on. Safe to re-run.

Four DynamoDB tables, an SQS queue with its dead-letter queue, and optionally
the two KMS signing keys. Every step checks before it creates, so running this
twice is a no-op and running it after a partial failure finishes the job.

    .venv/bin/python scripts/provision_aws.py --dry-run   # what it would do
    .venv/bin/python scripts/provision_aws.py             # tables + queue
    .venv/bin/python scripts/provision_aws.py --with-kms  # and the two keys

The table definitions are **not** duplicated here. They live in
`trustrail.stores.schema`, next to the stores that read them, and the same
function is what `tests/test_store_contract.py` runs against moto — so the
tables this creates are the tables the test suite has already exercised.

**Region defaults to ap-southeast-1** because that is where the Evaluator's
Bedrock model runs. Splitting the two across regions buys nothing and costs a
cross-region hop on every evaluation.

What this deliberately does not do:

- **No IAM roles.** A role wants a trust policy naming a principal that does not
  exist until App Runner is created, so it belongs to the deploy step, not here.
- **No merchant or agent tables.** Those two directories have no DynamoDB
  implementation, so they are in-memory and re-seeded at boot even on AWS.
  Creating tables nothing writes to would imply otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

DEFAULT_REGION = "ap-southeast-1"

QUEUE_NAME = "trustrail-settlement"
DLQ_NAME = "trustrail-settlement-dlq"

#: Deliveries before SQS moves a message to the dead-letter queue. Three is
#: enough to ride out an RPC hiccup and few enough that a charge which cannot
#: settle stops being retried before the mandate expires under it.
MAX_RECEIVE_COUNT = 3

#: Long enough for a settlement transaction to be mined and acknowledged.
#: Shorter than the mandate window on purpose: a message that reappears after
#: the mandate expired is a charge the contract will refuse anyway.
VISIBILITY_TIMEOUT_SECONDS = 120

#: KMS aliases. The two keys are separate because the roles are separate --
#: see CLAUDE.md. One key for both would undo the split at the key layer while
#: the contract still believed in it.
KMS_ALIASES = {
    "alias/trustrail-registrar": "Mandate Service: signs mandates and registerMandate",
    "alias/trustrail-settler": "Settlement Worker: signs spend()",
}
KMS_KEY_SPEC = "ECC_SECG_P256K1"
KMS_KEY_USAGE = "SIGN_VERIFY"


def main() -> int:
    args = _parse_args()
    import boto3

    session = boto3.session.Session(region_name=args.region)
    if session.get_credentials() is None:
        return _fail("no AWS credentials. Configure a profile or export keys.")

    print(f"region    {session.region_name}")
    print(f"account   {_account_id(session)}")
    print(f"mode      {'DRY RUN, nothing will be created' if args.dry_run else 'creating'}\n")

    created = _provision_tables(session, dry_run=args.dry_run)
    queue_url = _provision_queue(session, dry_run=args.dry_run)
    key_arns = (
        _provision_kms(session, dry_run=args.dry_run) if args.with_kms else {}
    )

    _report(created, queue_url, key_arns, dry_run=args.dry_run)
    return 0


def _provision_tables(session: Any, *, dry_run: bool) -> list[str]:
    """Create the four tables, with PITR on every one of them."""
    from trustrail.stores.dynamo import (
        AUDIT_TABLE,
        CONTROL_TABLE,
        MANDATES_TABLE,
        REVIEW_HOLDS_TABLE,
    )

    wanted = [MANDATES_TABLE, AUDIT_TABLE, CONTROL_TABLE, REVIEW_HOLDS_TABLE]
    client = session.client("dynamodb")
    existing = set(client.list_tables()["TableNames"])

    print("dynamodb")
    for name in wanted:
        print(f"  {'exists  ' if name in existing else 'create  '}{name}")
    missing = [name for name in wanted if name not in existing]

    if dry_run or not missing:
        return missing

    # One call for all four: `create_tables` is itself idempotent and is the
    # single definition of these schemas, shared with the moto contract test.
    from trustrail.stores.schema import create_tables

    create_tables(session.resource("dynamodb"))
    for name in missing:
        client.get_waiter("table_exists").wait(TableName=name)
    print(f"  created {len(missing)} table(s), point-in-time recovery on")
    return missing


def _provision_queue(session: Any, *, dry_run: bool) -> str | None:
    """Create the DLQ first, then the queue that redrives into it.

    Order matters: the redrive policy needs the dead-letter queue's ARN, so a
    queue created first would have to be reconfigured afterwards.
    """
    sqs = session.client("sqs")
    print("\nsqs")

    dlq_url = _existing_queue(sqs, DLQ_NAME)
    print(f"  {'exists  ' if dlq_url else 'create  '}{DLQ_NAME}")
    queue_url = _existing_queue(sqs, QUEUE_NAME)
    print(f"  {'exists  ' if queue_url else 'create  '}{QUEUE_NAME}")

    if dry_run:
        return queue_url

    if dlq_url is None:
        dlq_url = sqs.create_queue(QueueName=DLQ_NAME)["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(
        QueueUrl=dlq_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    redrive = json.dumps(
        {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": MAX_RECEIVE_COUNT}
    )
    if queue_url is None:
        queue_url = sqs.create_queue(
            QueueName=QUEUE_NAME,
            Attributes={
                "RedrivePolicy": redrive,
                "VisibilityTimeout": str(VISIBILITY_TIMEOUT_SECONDS),
            },
        )["QueueUrl"]
        print(f"  redrive after {MAX_RECEIVE_COUNT} attempts -> {DLQ_NAME}")
    else:
        # Re-applied rather than skipped: an existing queue may predate the
        # policy, and a settlement queue with no dead letter destination
        # retries a doomed charge forever.
        sqs.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={
                "RedrivePolicy": redrive,
                "VisibilityTimeout": str(VISIBILITY_TIMEOUT_SECONDS),
            },
        )
        print("  redrive policy re-applied")
    return queue_url


def _provision_kms(session: Any, *, dry_run: bool) -> dict[str, str]:
    """Create one secp256k1 signing key per role, addressed by alias.

    Aliases rather than raw key ids so the deployment names a role, not a
    generated identifier — and so rotating a key is an alias update.
    """
    kms = session.client("kms")
    print("\nkms")
    aliases = {a["AliasName"]: a.get("TargetKeyId") for a in _all_aliases(kms)}
    arns: dict[str, str] = {}
    minted: list[str] = []

    for alias, description in KMS_ALIASES.items():
        if alias in aliases:
            print(f"  exists  {alias}")
            if not dry_run:
                arns[alias] = kms.describe_key(KeyId=alias)["KeyMetadata"]["Arn"]
            continue
        print(f"  create  {alias}  ({KMS_KEY_SPEC}, {KMS_KEY_USAGE})")
        if dry_run:
            continue
        key = kms.create_key(
            Description=description,
            KeyUsage=KMS_KEY_USAGE,
            KeySpec=KMS_KEY_SPEC,
        )["KeyMetadata"]
        kms.create_alias(AliasName=alias, TargetKeyId=key["KeyId"])
        arns[alias] = key["Arn"]
        minted.append(alias)

    # Only for keys this run actually minted. Printed every time, it would
    # train whoever re-runs this to skip the one warning that matters.
    if minted:
        print(
            f"\n  NOTE: {len(minted)} new key(s) mean {len(minted)} new address(es).\n"
            "  Grant REGISTRAR_ROLE and SETTLER_ROLE to them on MandateRegistry,\n"
            "  and fund them with AVAX, or every register and spend reverts on\n"
            "  the role check."
        )
    return arns


def _report(
    tables: list[str], queue_url: str | None, key_arns: dict[str, str], *, dry_run: bool
) -> None:
    if dry_run:
        print("\nnothing was created. Re-run without --dry-run to apply.")
        return

    print("\n--- put these in the environment ---")
    print("TRUSTRAIL_PERSISTENCE=aws")
    if queue_url:
        print(f"TRUSTRAIL_QUEUE_URL={queue_url}")
    for alias, arn in key_arns.items():
        env = (
            "TRUSTRAIL_REGISTRAR_KMS_KEY"
            if "registrar" in alias
            else "TRUSTRAIL_SETTLER_KMS_KEY"
        )
        print(f"{env}={arn}")
    if not key_arns:
        print("# keys still come from .env; re-run with --with-kms to move them")


def _existing_queue(sqs: Any, name: str) -> str | None:
    from botocore.exceptions import ClientError

    try:
        return sqs.get_queue_url(QueueName=name)["QueueUrl"]
    except ClientError as error:
        if error.response["Error"]["Code"].endswith("NonExistentQueue"):
            return None
        raise


def _all_aliases(kms: Any) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    paginator = kms.get_paginator("list_aliases")
    for page in paginator.paginate():
        aliases.extend(page["Aliases"])
    return aliases


def _account_id(session: Any) -> str:
    return session.client("sts").get_caller_identity()["Account"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is missing without creating anything",
    )
    parser.add_argument(
        "--with-kms",
        action="store_true",
        help="also create the registrar and settler signing keys",
    )
    return parser.parse_args()


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
