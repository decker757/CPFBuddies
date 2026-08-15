"""The Onboarding Orchestrator: who is allowed to be a counterparty.

Registers merchant platforms and internal agent keypairs. Small, and entirely
load-bearing — the Verifier's counterparty checks and evidence checks both
reduce to "is there a record here, and does it match". An empty registry is not
a permissive system, it is one where every charge is a deterministic FAIL.

Two failure modes this file exists to make hard to hit:

- A merchant registered with a payout address copied from a listing rather than
  agreed out of band. The whole sub-seller protection is that the registry's
  address was established at onboarding, by us, not supplied at purchase time.
- An evaluator that signs under an id nobody registered. Workstream B's
  Evaluator stamps a *different* id depending on whether its model was
  reachable, so registering one of the two leaves a degraded evaluator
  producing evidence the Verifier rejects as unregistered. `register_evaluator`
  takes the ids as a set for that reason.
"""

from __future__ import annotations

from collections.abc import Iterable

from trustrail.models.registry import AgentRecord, AgentRole, MerchantRecord
from trustrail.ports import AgentDirectory, MerchantDirectory


class OnboardingOrchestrator:
    """Registers merchants and agents. Seeded by script for the demo."""

    def __init__(
        self, *, merchants: MerchantDirectory, agents: AgentDirectory
    ) -> None:
        self._merchants = merchants
        self._agents = agents

    def register_merchant(
        self, *, merchant_id: str, name: str, payout_address: str
    ) -> MerchantRecord:
        """Onboard a platform and fix the only address it can ever be paid at.

        `payout_address` is the platform's, agreed with the platform. It is not
        read from a listing, and the Verifier will reject any charge that tries
        to pay somewhere else — including one where a scam seller inside this
        very platform supplied their own address.
        """
        record = MerchantRecord(
            merchant_id=merchant_id, name=name, payout_address=payout_address
        )
        self._merchants.put(record)
        return record

    def suspend_merchant(self, merchant_id: str) -> MerchantRecord | None:
        """Deactivate a platform without forgetting it.

        Deleting the record would produce MERCHANT_NOT_REGISTERED, which reads
        as "we have never heard of them". Suspension says what actually
        happened, and the reason code the charge fails with says so too.
        """
        record = self._merchants.get(merchant_id)
        if record is None:
            return None
        suspended = record.model_copy(update={"is_active": False})
        self._merchants.put(suspended)
        return suspended

    def register_agent(
        self, *, agent_id: str, role: AgentRole, address: str
    ) -> AgentRecord:
        """Give an internal agent an identity its signatures can be checked against."""
        record = AgentRecord(agent_id=agent_id, role=role, address=address)
        self._agents.put(record)
        return record

    def register_evaluator(
        self, *, agent_ids: Iterable[str], address: str
    ) -> list[AgentRecord]:
        """Register every id one evaluator deployment can sign under.

        Workstream B's Evaluator reports `evaluator-rules-v1` when its model is
        unreachable and `evaluator-hybrid-nova-v1` when it is not. Both are the
        same process holding the same key, so both ids point at one address.
        Registering only the id you saw in testing means the first Bedrock
        outage turns every purchase into EVALUATOR_NOT_REGISTERED.
        """
        return [
            self.register_agent(
                agent_id=agent_id, role=AgentRole.EVALUATOR, address=address
            )
            for agent_id in agent_ids
        ]
