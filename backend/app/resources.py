"""Which implementations of the ports this process runs on.

Every store in this system sits behind a Protocol in `trustrail.ports`, and
there are two sets of implementations: in-memory ones that need nothing, and
AWS-backed ones that need an account. Choosing between them is the *only*
difference between a laptop and a deployment, and this module is the one place
that chooses. Nothing downstream — not the Mandate Service, not the
orchestrators, not the Verifier — can tell which set it was handed.

Two rules this module exists to keep:

**Memory is the default, everywhere, always.** `from_environment` opts in to AWS
on an explicit `TRUSTRAIL_PERSISTENCE=aws`, never by sniffing for credentials. A
developer with a configured AWS profile must not have their test run silently
become a write to a real table, and 411 tests currently depend on that.

**Partial persistence is stated, not hidden.** The merchant and agent
directories have no DynamoDB implementation yet, so in AWS mode they are still
in-memory and still seeded at boot. That is survivable — an unseeded registry
makes every charge a deterministic FAIL rather than failing open — but it is the
kind of thing that must be visible in a log line rather than discovered when a
restart loses every registered merchant.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from trustrail.ports import (
    AgentDirectory,
    AuditLog,
    KillSwitchStore,
    MandateStore,
    MerchantDirectory,
    ReviewHoldStore,
)
from trustrail.settlement.queue.base import SettlementQueue
from trustrail.settlement.queue.memory import InMemorySettlementQueue
from trustrail.stores.memory import (
    InMemoryAgentDirectory,
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
    InMemoryMerchantDirectory,
    InMemoryReviewHoldStore,
)

logger = logging.getLogger(__name__)

#: Set to `aws` to run on DynamoDB and SQS. Anything else, including unset,
#: means in-memory. Deliberately not a boolean: `TRUSTRAIL_PERSISTENCE=false`
#: reads as "no persistence", which is what memory is.
PERSISTENCE_ENV = "TRUSTRAIL_PERSISTENCE"
AWS_PERSISTENCE = "aws"

#: Where the settlement queue lives. No default: publishing to the wrong queue
#: is silent, and a queue nobody drains looks exactly like a rail that works
#: until you check whether anything settled.
QUEUE_URL_ENV = "TRUSTRAIL_QUEUE_URL"


@dataclass(frozen=True)
class Resources:
    """The seven ports a rail needs, already chosen.

    Typed as the Protocols rather than the concrete classes on purpose: a
    caller that can name `InMemoryAuditLog` is a caller that can accidentally
    depend on it.
    """

    mandates: MandateStore
    kill_switch: KillSwitchStore
    audit: AuditLog
    merchants: MerchantDirectory
    agents: AgentDirectory
    holds: ReviewHoldStore
    queue: SettlementQueue

    #: True when this set will survive a restart. Not decoration -- the demo
    #: script prints it, because "nothing persisted" and "everything persisted"
    #: look identical for exactly as long as the process stays up.
    durable: bool = False

    @classmethod
    def in_memory(cls) -> Resources:
        return in_memory()

    @classmethod
    def from_aws(cls, *, queue_url: str | None = None) -> Resources:
        return from_aws(queue_url=queue_url)

    @classmethod
    def from_environment(cls) -> Resources:
        return from_environment()


def in_memory() -> Resources:
    """Everything in this process. Loses everything on restart, and that is fine locally."""
    return Resources(
        mandates=InMemoryMandateStore(),
        kill_switch=InMemoryKillSwitchStore(),
        audit=InMemoryAuditLog(),
        merchants=InMemoryMerchantDirectory(),
        agents=InMemoryAgentDirectory(),
        holds=InMemoryReviewHoldStore(),
        queue=InMemorySettlementQueue(),
        durable=False,
    )


def from_aws(*, queue_url: str | None = None) -> Resources:
    """DynamoDB for state, SQS for settlement. Requires tables that exist.

    Imported lazily so that a machine with no boto3 and no credentials can
    still import this module, which the offline test suite does on every run.

    `queue_url` falls back to the environment. Without either, the settlement
    queue stays in-memory and says so — a rail that refuses to start because a
    queue is missing is worse than one that verifies charges and holds them,
    especially mid-demo.
    """
    from trustrail.stores.dynamo import (
        DynamoAuditLog,
        DynamoKillSwitchStore,
        DynamoMandateStore,
        DynamoReviewHoldStore,
    )

    resolved_url = queue_url or os.environ.get(QUEUE_URL_ENV)
    if resolved_url:
        from trustrail.settlement.queue.sqs import SqsQueue

        queue: SettlementQueue = SqsQueue(resolved_url)
    else:
        logger.warning(
            "%s is unset; the settlement queue is in-memory and a restart will "
            "drop every charge waiting to settle",
            QUEUE_URL_ENV,
        )
        queue = InMemorySettlementQueue()

    # Said out loud rather than left to be discovered. These two have no
    # DynamoDB implementation, so they are re-seeded at boot and a restart
    # forgets every merchant and evaluator key registered at runtime.
    logger.warning(
        "merchant and agent registries are in-memory even on AWS: no Dynamo "
        "implementation exists for them, so both are re-seeded at boot"
    )

    return Resources(
        mandates=DynamoMandateStore(),
        kill_switch=DynamoKillSwitchStore(),
        audit=DynamoAuditLog(),
        merchants=InMemoryMerchantDirectory(),
        agents=InMemoryAgentDirectory(),
        holds=DynamoReviewHoldStore(),
        queue=queue,
        durable=True,
    )


def from_environment() -> Resources:
    """Memory unless `TRUSTRAIL_PERSISTENCE=aws` says otherwise.

    Opt-in, never inferred. Sniffing for credentials would mean a developer who
    happens to have an AWS profile configured runs their tests against real
    tables, which is a bad afternoon and an expensive one.
    """
    choice = os.environ.get(PERSISTENCE_ENV, "").strip().lower()
    if choice != AWS_PERSISTENCE:
        return in_memory()
    logger.info("persistence: DynamoDB and SQS")
    return from_aws()
