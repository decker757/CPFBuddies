"""The rail boundary.

CLAUDE.md: "Keep rail selection behind one interface so the mandate check is rail-agnostic."

A rail receives an instruction and reports what happened. It does not decide whether the
payment is allowed -- that already happened in the Verifier, and for the onchain rail it
happens again in the contract. Adding a rail is a new file implementing this protocol; the
worker never learns about it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trustrail.settlement.models import SettlementInstruction, SettlementReceipt


@runtime_checkable
class SettlementRail(Protocol):
    """Anything that can move money for a settlement instruction."""

    @property
    def name(self) -> str:
        """Stable identifier used to select this rail from configuration."""
        ...

    def settle(self, instruction: SettlementInstruction) -> SettlementReceipt:
        """Attempt the payment. Must not raise for an ordinary refusal."""
        ...
