"""The Mandate Service: what it will and will not let you do.

The interesting tests here are the refusals. Minting works; the reason this file
matters is that binding cannot widen a mandate and consumption cannot happen
twice, whatever order things arrive in.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from trustrail.errors import (
    IllegalBinding,
    MandateNotFound,
    MandateStatusConflict,
    NonceAlreadyClaimed,
)
from trustrail.mandate.service import MandateService
from trustrail.models.audit import AuditEventType
from trustrail.models.mandate import MandateBinding, MandateRecord, MandateStatus
from trustrail.models.money import Currency, Money
from trustrail.models.primitives import to_bytes
from trustrail.signing.crypto import signed_by
from trustrail.signing.local import LocalSigner
from trustrail.stores.memory import InMemoryAuditLog, InMemoryMandateStore

CAP = Money(currency=Currency.XSGD, amount="5.00")
BINDING = MandateBinding(
    merchant_address="0x" + "ab" * 20, basket_hash="0x" + "cd" * 32
)


def _mint(service: MandateService, **overrides: object) -> MandateRecord:
    fields = {
        "principal": "0x" + "11" * 20,
        "agent_id": "browser-1",
        "max_amount": CAP,
        "intent": "toothbrush under $5",
        "ttl": timedelta(minutes=10),
    }
    return service.mint(**(fields | overrides))


def _with_mandate_id(record: MandateRecord, mandate_id: str) -> MandateRecord:
    """The same record under a different id, keeping the original nonce."""
    mandate = record.signed.mandate.model_copy(update={"mandate_id": mandate_id})
    return record.model_copy(
        update={"signed": record.signed.model_copy(update={"mandate": mandate})}
    )


# --- minting ---------------------------------------------------------------


def test_a_minted_mandate_has_a_budget_but_no_product(
    mandates: MandateService,
) -> None:
    """The human approved a budget and an intent, not a SKU."""
    record = _mint(mandates)

    assert record.status is MandateStatus.MINTED
    assert record.signed.mandate.max_amount == CAP
    assert record.signed.mandate.merchant_address is None
    assert record.signed.mandate.basket_hash is None
    assert not record.signed.mandate.is_bound


def test_a_minted_mandate_carries_a_verifiable_issuer_signature(
    mandates: MandateService, signer: LocalSigner
) -> None:
    signed = _mint(mandates).signed

    assert signed_by(to_bytes(signed.digest), signed.signature, signer.address)


def test_each_mint_gets_its_own_id_and_nonce(mandates: MandateService) -> None:
    first, second = _mint(mandates), _mint(mandates)

    assert first.mandate_id != second.mandate_id
    assert first.signed.mandate.nonce != second.signed.mandate.nonce


def test_a_reused_nonce_is_refused(
    mandates: MandateService, store: InMemoryMandateStore
) -> None:
    """Nonce collision would break one-time consumption, so it is never allowed."""
    record = _mint(mandates)
    same_nonce_different_id = _with_mandate_id(record, "0x" + "ee" * 32)

    with pytest.raises(NonceAlreadyClaimed):
        store.save_new(same_nonce_different_id)


def test_the_store_indexes_a_mandate_by_its_nonce(
    mandates: MandateService, store: InMemoryMandateStore
) -> None:
    """The Verifier's replay check reads this index."""
    record = _mint(mandates)

    assert store.nonce_owner(record.signed.mandate.nonce) == record.mandate_id


# --- binding ---------------------------------------------------------------


def test_binding_narrows_the_mandate_and_leaves_the_cap_alone(
    mandates: MandateService,
) -> None:
    minted = _mint(mandates)

    bound = mandates.bind(minted.mandate_id, BINDING, approved_by="ernest")

    assert bound.status is MandateStatus.BOUND
    assert bound.signed.mandate.max_amount == minted.signed.mandate.max_amount
    assert bound.signed.mandate.expires_at == minted.signed.mandate.expires_at
    assert bound.signed.mandate.merchant_address == BINDING.merchant_address
    assert bound.signed.mandate.basket_hash == BINDING.basket_hash


def test_binding_re_signs_so_the_digest_changes(
    mandates: MandateService, signer: LocalSigner
) -> None:
    """A bound mandate is a different credential, and says so cryptographically."""
    minted = _mint(mandates)

    bound = mandates.bind(minted.mandate_id, BINDING, approved_by="ernest")

    assert bound.signed.digest != minted.signed.digest
    assert signed_by(
        to_bytes(bound.signed.digest), bound.signed.signature, signer.address
    )


def test_a_mandate_cannot_be_bound_twice(mandates: MandateService) -> None:
    """Otherwise a second approval could redirect an already-approved purchase."""
    record = _mint(mandates)
    mandates.bind(record.mandate_id, BINDING, approved_by="ernest")

    with pytest.raises(IllegalBinding):
        mandates.bind(record.mandate_id, BINDING, approved_by="ernest")


def test_a_revoked_mandate_cannot_be_bound(mandates: MandateService) -> None:
    record = _mint(mandates)
    mandates.revoke(record.mandate_id, actor="ernest", reason="changed my mind")

    with pytest.raises(IllegalBinding):
        mandates.bind(record.mandate_id, BINDING, approved_by="ernest")


def test_binding_cannot_carry_a_new_cap() -> None:
    """Structural, not checked at runtime: the type has nowhere to put one."""
    with pytest.raises(ValueError):
        MandateBinding(
            merchant_address="0x" + "ab" * 20,
            basket_hash="0x" + "cd" * 32,
            max_amount=Money(currency=Currency.XSGD, amount="5000.00"),
        )


# --- consumption and revocation --------------------------------------------


def test_a_mandate_can_only_be_consumed_once(mandates: MandateService) -> None:
    record = _mint(mandates)
    mandates.consume(record.mandate_id, actor="settlement-worker")

    with pytest.raises(MandateStatusConflict):
        mandates.consume(record.mandate_id, actor="settlement-worker")


def test_concurrent_consumption_produces_exactly_one_winner(
    mandates: MandateService,
) -> None:
    """Two settlement workers racing on one mandate. Only one may spend it."""
    record = _mint(mandates)

    def attempt() -> bool:
        try:
            mandates.consume(record.mandate_id, actor="settlement-worker")
        except MandateStatusConflict:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(8)))

    assert outcomes.count(True) == 1


def test_a_consumed_mandate_cannot_be_revoked(mandates: MandateService) -> None:
    record = _mint(mandates)
    mandates.consume(record.mandate_id, actor="settlement-worker")

    with pytest.raises(MandateStatusConflict):
        mandates.revoke(record.mandate_id, actor="ernest", reason="too late")


def test_operating_on_an_unknown_mandate_is_an_error(
    mandates: MandateService,
) -> None:
    with pytest.raises(MandateNotFound):
        mandates.revoke("0x" + "00" * 32, actor="ernest", reason="nothing there")


# --- kill switch -----------------------------------------------------------


def test_the_global_kill_switch_halts_every_principal(
    mandates: MandateService,
) -> None:
    mandates.halt_all(active=True, actor="ops")

    assert mandates.is_halted("0x" + "11" * 20)
    assert mandates.is_halted("0x" + "22" * 20)


def test_a_principal_kill_switch_halts_only_that_principal(
    mandates: MandateService,
) -> None:
    halted, unaffected = "0x" + "11" * 20, "0x" + "22" * 20

    mandates.halt_principal(halted, active=True, actor="ops")

    assert mandates.is_halted(halted)
    assert not mandates.is_halted(unaffected)


def test_the_kill_switch_can_be_released(mandates: MandateService) -> None:
    mandates.halt_all(active=True, actor="ops")
    mandates.halt_all(active=False, actor="ops")

    assert not mandates.is_halted("0x" + "11" * 20)


# --- audit trail -----------------------------------------------------------


def test_every_lifecycle_step_is_recorded(
    mandates: MandateService, audit: InMemoryAuditLog
) -> None:
    record = _mint(mandates)
    mandates.bind(record.mandate_id, BINDING, approved_by="ernest")
    mandates.consume(record.mandate_id, actor="settlement-worker")

    events = [entry.event_type for entry in audit.list_for_mandate(record.mandate_id)]

    assert events == [
        AuditEventType.MANDATE_MINTED,
        AuditEventType.MANDATE_BOUND,
        AuditEventType.MANDATE_CONSUMED,
    ]


def test_a_rejected_charge_is_audited_as_carefully_as_an_approved_one(
    mandates: MandateService, audit: InMemoryAuditLog, verifier, build
) -> None:
    """A FAIL nobody can point to afterwards is not an auditable system."""
    verdict = verifier.verify(build.request(kill_switch_active=True))

    mandates.record_verdict(verdict)

    entry = audit.list_for_mandate(verdict.mandate_id)[0]
    assert entry.event_type is AuditEventType.VERDICT_ISSUED
    assert entry.verdict is not None
    assert "KILL_SWITCH_ACTIVE" in entry.summary
