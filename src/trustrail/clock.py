"""Time, injected rather than read.

Expiry is a deterministic FAIL, so "what time is it" is load-bearing. Passing a
clock in means the boundary cases — one second before expiry, one second after,
clock skew — are ordinary unit tests rather than sleeps.
"""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Wall-clock UTC. The production implementation."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock that only moves when a test moves it."""

    def __init__(self, at: datetime) -> None:
        self._now = _require_aware(at)

    def now(self) -> datetime:
        return self._now

    def advance_to(self, at: datetime) -> None:
        self._now = _require_aware(at)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("clock times must be timezone-aware")
    return value
