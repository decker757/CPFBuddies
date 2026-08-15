"""Money as an exact value object.

The mandate cap is the load-bearing arithmetic in this system: "over cap" is a
FAIL that no human may override, so the comparison behind it has to be exact.
Every amount is therefore carried as a decimal string on the wire and compared
as integer minor units internally. No float ever touches an amount.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import total_ordering
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, model_validator


class Currency(StrEnum):
    """Currencies this system can parse.

    Only XSGD is settleable — that is a track rule, enforced by the Verifier
    rather than by this enum. The others exist because a merchant can quote in
    them, and we would rather reject that with a reason code than fail to parse
    the payload at all.
    """

    XSGD = "XSGD"
    SGD = "SGD"
    USD = "USD"


#: Smallest-unit precision per currency. XSGD is the ERC-20 on Avalanche
#: C-Chain; track C must confirm 6 against the deployed token before mainnet.
CURRENCY_DECIMALS: dict[Currency, int] = {
    Currency.XSGD: 6,
    Currency.SGD: 2,
    Currency.USD: 2,
}

#: Amounts always render with at least this many decimal places, so "5" and
#: "5.0" both round-trip as "5.00" and fixtures stay byte-stable.
_MIN_DISPLAY_DECIMALS = 2

#: Minor units are encoded as a Solidity `uint256` in the mandate digest and
#: passed to the contract as one. Anything larger has no onchain
#: representation, so it is rejected here rather than overflowing later.
_MAX_MINOR_UNITS = 2**256 - 1


@total_ordering
class Money(BaseModel):
    """A non-negative amount in a known currency.

    Equality and ordering compare exact minor units. Comparing across
    currencies raises: the Verifier rejects a currency mismatch with a reason
    code before any comparison happens, so reaching that raise means a caller
    skipped the pipeline.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: Currency
    amount: str

    @model_validator(mode="before")
    @classmethod
    def _normalise_amount(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "amount" not in data or "currency" not in data:
            return data
        currency = Currency(data["currency"])
        parsed = _parse_decimal(data["amount"])
        _reject_excess_precision(parsed, currency)
        _reject_unrepresentable(parsed, currency)
        return {"currency": currency, "amount": _format_amount(parsed)}

    @classmethod
    def from_minor_units(cls, minor_units: int, currency: Currency) -> Self:
        """Build an amount from smallest units, e.g. 4_200_000 XSGD -> "4.20"."""
        whole, fraction = divmod(minor_units, 10 ** CURRENCY_DECIMALS[currency])
        return cls(
            currency=currency,
            amount=f"{whole}.{fraction:0{CURRENCY_DECIMALS[currency]}d}",
        )

    @property
    def minor_units(self) -> int:
        """The amount in smallest units. This is what onchain enforcement uses."""
        return _to_minor_units(Decimal(self.amount), self.currency)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

    def __lt__(self, other: Money) -> bool:
        self._assert_comparable(other)
        return self.minor_units < other.minor_units

    def _assert_comparable(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot compare Money with {type(other).__name__}")
        if self.currency is not other.currency:
            raise ValueError(
                f"refusing to compare {self.currency} with {other.currency}; "
                "check currency equality before comparing amounts"
            )


def _parse_decimal(raw: Any) -> Decimal:
    if isinstance(raw, float):
        raise ValueError("amounts must be strings, not floats, to stay exact")
    try:
        parsed = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"{raw!r} is not a valid decimal amount") from exc
    if not parsed.is_finite():
        raise ValueError("amount must be finite")
    if parsed < 0:
        raise ValueError("amount must not be negative")
    return parsed


def _reject_excess_precision(amount: Decimal, currency: Currency) -> None:
    """Reject precision the currency cannot represent, rather than rounding it.

    Silently rounding here would let a charge sit a fraction above the cap and
    still compare equal to it.
    """
    decimals = CURRENCY_DECIMALS[currency]
    used = -amount.as_tuple().exponent
    if used > decimals:
        raise ValueError(f"{currency} supports {decimals} decimal places, got {used}")


def _reject_unrepresentable(amount: Decimal, currency: Currency) -> None:
    """Reject amounts with no onchain representation.

    Without this, an absurd cap parses happily and then overflows when the
    EIP-712 encoder tries to pack it into a 32-byte word — a crash at signing
    time instead of a validation error at the edge.
    """
    if _to_minor_units(amount, currency) > _MAX_MINOR_UNITS:
        raise ValueError("amount is too large to represent onchain")


def _to_minor_units(amount: Decimal, currency: Currency) -> int:
    """Scale to smallest units using integer arithmetic only.

    `Decimal.scaleb` and `Decimal.normalize` both round to the ambient decimal
    context — 28 significant digits by default — which is silent precision loss
    in the one place this system cannot afford it. Going through the digit tuple
    keeps the conversion exact at any magnitude.
    """
    _, digits, exponent = amount.as_tuple()
    shift = int(exponent) + CURRENCY_DECIMALS[currency]
    return int("".join(map(str, digits))) * 10**shift


def _format_amount(amount: Decimal) -> str:
    whole, _, fraction = format(amount, "f").partition(".")
    trimmed = fraction.rstrip("0")
    return f"{whole}.{trimmed.ljust(_MIN_DISPLAY_DECIMALS, '0')}"
