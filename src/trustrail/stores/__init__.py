"""Persistence adapters behind the ports in `trustrail.ports`.

`memory` is the default: it runs the tests and the offline demo. `dynamo` is the
deployed path. Nothing above this package knows which one it is talking to.

Only the in-memory stores are re-exported here. `trustrail.stores.dynamo`
imports boto3, so it stays behind an explicit import — nothing in the core path
should drag an AWS SDK in just by importing this package.
"""

from trustrail.stores.memory import (
    InMemoryAgentDirectory,
    InMemoryAuditLog,
    InMemoryKillSwitchStore,
    InMemoryMandateStore,
    InMemoryMerchantDirectory,
    InMemoryReviewHoldStore,
)

__all__ = [
    "InMemoryAgentDirectory",
    "InMemoryAuditLog",
    "InMemoryKillSwitchStore",
    "InMemoryMandateStore",
    "InMemoryMerchantDirectory",
    "InMemoryReviewHoldStore",
]
