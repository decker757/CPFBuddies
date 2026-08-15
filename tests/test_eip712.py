"""The mandate digest, pinned.

Track C's MandateRegistry contract has to recompute this exact value in
Solidity. The golden digest below is therefore a cross-workstream contract: if
it changes, the contract's `spend()` stops matching and settlement breaks. A
failure here means "tell workstream C", not "update the constant".
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from trustrail.contracts.scenarios import BASKET_HASH, MERCHANT_PAYOUT, ScenarioBuilder
from trustrail.models.money import Currency, Money
from trustrail.signing.eip712 import (
    AVALANCHE_MAINNET_CHAIN_ID,
    Eip712Domain,
    mandate_digest,
)

#: The digest of the canonical demo mandate under the default (Fuji) domain.
GOLDEN_DIGEST = "0x4a52183cc1bf6bc799f10ce0438cb0e0d87b182803c6be96bc7f17bcfe16db35"


def test_the_demo_mandate_digest_is_stable(build: ScenarioBuilder) -> None:
    assert mandate_digest(build.mandate(), build.domain) == GOLDEN_DIGEST


def test_a_digest_is_thirty_two_bytes(build: ScenarioBuilder) -> None:
    digest = mandate_digest(build.mandate(), build.domain)

    assert len(bytes.fromhex(digest.removeprefix("0x"))) == 32


@pytest.mark.parametrize(
    "change",
    [
        {"mandate_id": "0x" + "01" * 32},
        {"principal": "0x" + "02" * 20},
        {"agent_id": "browser-2"},
        {"max_amount": Money(currency=Currency.XSGD, amount="5.01")},
        {"intent": "toothbrush under $6"},
        {"nonce": "0x" + "03" * 32},
        {"merchant_address": MERCHANT_PAYOUT},
        {"basket_hash": BASKET_HASH},
    ],
    ids=lambda change: next(iter(change)),
)
def test_changing_any_field_changes_the_digest(
    build: ScenarioBuilder, change: dict
) -> None:
    """Every field is actually covered by the signature, not just carried near it."""
    original = mandate_digest(build.mandate(), build.domain)

    assert mandate_digest(build.mandate(**change), build.domain) != original


def test_changing_the_expiry_changes_the_digest(build: ScenarioBuilder) -> None:
    base = build.mandate()
    later = build.mandate(expires_at=base.expires_at + timedelta(seconds=1))

    assert mandate_digest(later, build.domain) != mandate_digest(base, build.domain)


def test_currency_is_covered_even_when_the_number_is_the_same(
    build: ScenarioBuilder,
) -> None:
    """S$5 and US$5 are different approvals and must not share a digest."""
    in_xsgd = build.mandate()
    in_usd = build.mandate(max_amount=Money(currency=Currency.USD, amount="5.00"))

    assert mandate_digest(in_usd, build.domain) != mandate_digest(
        in_xsgd, build.domain
    )


def test_binding_a_mandate_produces_a_different_credential(
    build: ScenarioBuilder,
) -> None:
    unbound = build.mandate()
    bound = build.mandate(
        merchant_address=MERCHANT_PAYOUT, basket_hash=BASKET_HASH
    )

    assert mandate_digest(bound, build.domain) != mandate_digest(
        unbound, build.domain
    )


def test_a_mandate_is_not_valid_on_another_chain(build: ScenarioBuilder) -> None:
    """The domain separator is why a Fuji mandate cannot be replayed on mainnet."""
    mainnet = Eip712Domain(chain_id=AVALANCHE_MAINNET_CHAIN_ID)

    assert mandate_digest(build.mandate(), mainnet) != mandate_digest(
        build.mandate(), build.domain
    )


def test_a_mandate_is_not_valid_against_another_contract(
    build: ScenarioBuilder,
) -> None:
    other_registry = Eip712Domain(verifying_contract="0x" + "99" * 20)

    assert mandate_digest(build.mandate(), other_registry) != mandate_digest(
        build.mandate(), build.domain
    )
