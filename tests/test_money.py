"""Money must be exact, because the cap is a FAIL nobody can override.

Anything that rounds, truncates, or goes through a float would let a charge sit
a fraction above the cap and still compare as equal to it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trustrail.models.money import Currency, Money


def xsgd(amount: str) -> Money:
    return Money(currency=Currency.XSGD, amount=amount)


@pytest.mark.parametrize(
    ("written", "normalised", "minor_units"),
    [
        ("4.20", "4.20", 4_200_000),
        ("4.2", "4.20", 4_200_000),
        ("4.200000", "4.20", 4_200_000),
        ("5", "5.00", 5_000_000),
        ("0", "0.00", 0),
        ("0.000001", "0.000001", 1),
        ("1234.567891", "1234.567891", 1_234_567_891),
    ],
)
def test_amounts_normalise_to_one_canonical_form(
    written: str, normalised: str, minor_units: int
) -> None:
    """Fixtures stay byte-stable and equal amounts are literally equal."""
    amount = xsgd(written)

    assert amount.amount == normalised
    assert amount.minor_units == minor_units


def test_equal_amounts_written_differently_are_equal() -> None:
    assert xsgd("4.2") == xsgd("4.200000")


def test_round_trips_through_minor_units() -> None:
    assert Money.from_minor_units(4_200_000, Currency.XSGD) == xsgd("4.20")


@pytest.mark.parametrize(
    "invalid",
    ["4.2055555555", "-1.00", "abc", "NaN", "Infinity"],
)
def test_unusable_amounts_are_rejected_at_the_edge(invalid: str) -> None:
    with pytest.raises(ValidationError):
        xsgd(invalid)


def test_amounts_too_large_for_uint256_are_rejected() -> None:
    """The EIP-712 encoder packs minor units into a 32-byte word, and so does
    the contract. An amount that cannot fit must fail here, not there."""
    with pytest.raises(ValidationError, match="too large to represent onchain"):
        xsgd("1e5000")


def test_the_largest_representable_amount_is_accepted() -> None:
    largest = Money.from_minor_units(2**256 - 1, Currency.XSGD)

    assert largest.minor_units == 2**256 - 1


def test_floats_are_rejected_outright() -> None:
    """0.1 + 0.2 has no place anywhere near a payment cap."""
    with pytest.raises(ValidationError):
        Money(currency=Currency.XSGD, amount=4.20)


def test_precision_beyond_the_currency_is_rejected_not_rounded() -> None:
    """Rounding here would let a charge hide just above the cap."""
    with pytest.raises(ValidationError):
        Money(currency=Currency.SGD, amount="4.201")


def test_currencies_carry_their_own_precision() -> None:
    assert Money(currency=Currency.SGD, amount="4.20").minor_units == 420
    assert xsgd("4.20").minor_units == 4_200_000


def test_comparison_across_currencies_refuses_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="refusing to compare"):
        _ = xsgd("4.20") < Money(currency=Currency.USD, amount="4.20")


def test_ordering_is_exact_at_the_minor_unit() -> None:
    assert xsgd("4.999999") < xsgd("5.00")
    assert xsgd("5.000001") > xsgd("5.00")
    assert xsgd("5.00") >= xsgd("5.00")


def test_amounts_are_immutable() -> None:
    """A cap that could be mutated after signing would not be a cap."""
    with pytest.raises(ValidationError):
        xsgd("4.20").amount = "9999.00"


def test_unknown_currencies_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Money(currency="DOGE", amount="4.20")
