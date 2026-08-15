"""Settlement worker behaviour.

The load-bearing distinction is REFUSED versus ERROR. A reverted transaction retried forever
burns gas and never settles; a transient RPC fault that is never retried loses a payment.

Verdicts here come from the real Verifier, so a change to A's decision logic surfaces as a
failure in C rather than as a surprise at integration time.
"""

from __future__ import annotations

import pytest

from trustrail.clock import FrozenClock
from trustrail.contracts.scenarios import DEMO_NOW, ScenarioBuilder
from trustrail.models.audit import AuditEventType, SettlementOutcome
from trustrail.models.money import Currency, Money
from trustrail.models.verdict import Decision, ReasonCode
from trustrail.settlement.models import (
    SettlementInstruction,
    SettlementReceipt,
    SettlementRequest,
)
from trustrail.settlement.queue.memory import InMemorySettlementQueue
from trustrail.settlement.rails.base import SettlementRail
from trustrail.settlement.worker import SettlementWorker
from trustrail.stores.memory import InMemoryAuditLog
from trustrail.verifier.service import VerifierService
from tests.settlement_support import settlement_request


class FakeRail:
    """A rail that returns whatever outcome the test asks for."""

    def __init__(
        self, outcome: SettlementOutcome = SettlementOutcome.SETTLED, name: str = "fake"
    ) -> None:
        self._outcome = outcome
        self._name = name
        self.calls: list[SettlementInstruction] = []

    @property
    def name(self) -> str:
        return self._name

    def settle(self, instruction: SettlementInstruction) -> SettlementReceipt:
        self.calls.append(instruction)
        return SettlementReceipt(
            mandate_id=instruction.mandate_id,
            rail=self._name,
            status=self._outcome,
            reference="0xabc" if self._outcome is not SettlementOutcome.ERROR else None,
            explorer_url="https://snowtrace.io/tx/0xabc"
            if self._outcome is not SettlementOutcome.ERROR
            else None,
            reason_code=ReasonCode.CHARGE_OVER_CAP
            if self._outcome is SettlementOutcome.REFUSED
            else None,
            detail="fake",
        )


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(DEMO_NOW)


def build_worker(
    clock: FrozenClock,
    outcome: SettlementOutcome = SettlementOutcome.SETTLED,
    max_receives: int = 3,
):
    queue = InMemorySettlementQueue(max_receives=max_receives)
    rail = FakeRail(outcome)
    audit = InMemoryAuditLog()
    worker = SettlementWorker(queue, {rail.name: rail}, audit, clock, rail_name=rail.name)
    return queue, rail, audit, worker


class TestConfiguration:
    def test_rejects_an_unconfigured_rail_name(self, clock):
        with pytest.raises(ValueError, match="not configured"):
            SettlementWorker(
                InMemorySettlementQueue(),
                {"fake": FakeRail()},
                InMemoryAuditLog(),
                clock,
                rail_name="nope",
            )

    def test_selects_the_configured_rail(self, clock):
        onchain, card = FakeRail(name="x402-onchain"), FakeRail(name="straitsx-card")
        worker = SettlementWorker(
            InMemorySettlementQueue(),
            {onchain.name: onchain, card.name: card},
            InMemoryAuditLog(),
            clock,
            rail_name="straitsx-card",
        )
        assert worker.rail is card
        assert isinstance(card, SettlementRail)


class TestQueuePayload:
    def test_the_verifier_really_passes_the_happy_scenario(self, build, verifier):
        # If this ever fails, A and C have stopped agreeing and every test below is moot.
        request = settlement_request(build, verifier)
        assert request.verdict.decision is Decision.PASS
        assert request.settleable

    def test_rejects_a_payload_whose_parts_describe_different_purchases(
        self, build: ScenarioBuilder, verifier: VerifierService
    ):
        request = settlement_request(build, verifier)
        other_mandate = build.sign_mandate(
            build.mandate(mandate_id="0x" + "cd" * 32, nonce="0x" + "ef" * 32)
        )
        with pytest.raises(ValueError, match="different mandate"):
            SettlementRequest(
                verdict=request.verdict,
                charge=request.charge,
                signed_mandate=other_mandate,
            )


class TestSettled:
    def test_settles_and_acks(self, clock, build, verifier):
        queue, rail, audit, worker = build_worker(clock)
        queue.publish(settlement_request(build, verifier))

        receipts = worker.process_once()

        assert [r.status for r in receipts] == [SettlementOutcome.SETTLED]
        assert rail.calls[0].amount == Money(currency=Currency.XSGD, amount="4.20")
        assert queue.pending == [] and queue.in_flight == []

        entry = audit.all_entries()[0]
        assert entry.event_type is AuditEventType.SETTLEMENT_SETTLED
        assert entry.settlement is not None
        assert entry.settlement.explorer_url == "https://snowtrace.io/tx/0xabc"
        assert entry.mandate_id == rail.calls[0].mandate_id

    def test_audit_entry_is_readable_by_mandate(self, clock, build, verifier):
        queue, rail, audit, worker = build_worker(clock)
        request = settlement_request(build, verifier)
        queue.publish(request)

        worker.process_once()

        mandate_id = request.signed_mandate.mandate.mandate_id
        assert len(audit.list_for_mandate(mandate_id)) == 1


class TestRefused:
    def test_acks_rather_than_retrying(self, clock, build, verifier):
        # The contract said no. Redelivering will not change its mind.
        queue, _, audit, worker = build_worker(clock, SettlementOutcome.REFUSED)
        queue.publish(settlement_request(build, verifier))

        worker.process_once()

        assert queue.pending == [] and queue.dead_letter == []
        entry = audit.all_entries()[0]
        assert entry.event_type is AuditEventType.SETTLEMENT_REFUSED
        assert entry.settlement.reason_code is ReasonCode.CHARGE_OVER_CAP


class TestError:
    def test_nacks_so_the_message_is_retried(self, clock, build, verifier):
        queue, _, audit, worker = build_worker(clock, SettlementOutcome.ERROR)
        queue.publish(settlement_request(build, verifier))

        worker.process_once()

        assert len(queue.pending) == 1
        assert audit.all_entries()[0].event_type is AuditEventType.SETTLEMENT_FAILED

    def test_dead_letters_once_attempts_are_exhausted(self, clock, build, verifier):
        queue, _, audit, worker = build_worker(
            clock, SettlementOutcome.ERROR, max_receives=2
        )
        queue.publish(settlement_request(build, verifier))

        worker.process_once()
        worker.process_once()

        assert queue.pending == []
        assert len(queue.dead_letter) == 1
        assert len(audit.all_entries()) == 2


class TestNonPassVerdicts:
    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            ("over cap", {"charge_amount": Money(currency=Currency.XSGD, amount="99.00")}),
            ("kill switch", {"kill_switch_active": True}),
        ],
    )
    def test_refuses_to_settle_and_never_reaches_the_rail(
        self, clock, build, verifier, label, overrides
    ):
        queue, rail, audit, worker = build_worker(clock)

        if "charge_amount" in overrides:
            charge = build.charge(amount=overrides["charge_amount"])
            request = settlement_request(
                build,
                verifier,
                charge=charge,
                evaluation=build.sign_evaluation(build.evaluation(charge)),
            )
        else:
            request = settlement_request(build, verifier, **overrides)

        assert request.verdict.decision is not Decision.PASS, label
        queue.publish(request)

        receipts = worker.process_once()

        assert receipts[0].status is SettlementOutcome.REFUSED
        assert rail.calls == [], "a non-PASS must never reach a rail"
        assert "not settleable" in audit.all_entries()[0].settlement.detail
        # Acked, not retried: redelivery cannot turn a FAIL into a payment.
        assert queue.pending == [] and queue.dead_letter == []

    def test_instruction_refuses_to_build_from_a_non_pass(self, build, verifier):
        charge = build.charge(amount=Money(currency=Currency.XSGD, amount="99.00"))
        request = settlement_request(
            build,
            verifier,
            charge=charge,
            evaluation=build.sign_evaluation(build.evaluation(charge)),
        )
        with pytest.raises(ValueError, match="only PASS decisions"):
            SettlementInstruction.from_request(request)
