"""The verdict: three outcomes, and an honest account of how we got there.

The split between FAIL and REVIEW is by whether the failing check is a fact or a
judgement call:

- **FAIL** — cryptographic or arithmetic. Bad signature, expired, revoked, over
  cap, replayed, unregistered merchant, payout mismatch. No override, ever. If
  the buyer wants to spend more, that is a new mandate, not an approval button.
- **REVIEW** — a human decides. Middle-band risk, suspected substitution,
  suspiciously low price, injection suspected but not confirmed.
- **PASS** — everything clean.

Every check in the trace is tagged `DETERMINISTIC` or `JUDGEMENT`. A FAIL driven
by a risk score is a threshold in config, not a fact about the world, and the
payload says so rather than letting the two look alike.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from trustrail.models.evaluation import MAX_RISK_SCORE, MIN_RISK_SCORE
from trustrail.models.primitives import Hex32, Timestamp


class Decision(StrEnum):
    PASS = "PASS"
    REVIEW = "REVIEW"
    FAIL = "FAIL"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]

    @classmethod
    def worst(cls, decisions: list[Self]) -> Self:
        """The strictest decision in the list; PASS when the list is empty."""
        return max(decisions, key=lambda decision: decision.severity, default=cls.PASS)


_SEVERITY: dict[Decision, int] = {
    Decision.PASS: 0,
    Decision.REVIEW: 1,
    Decision.FAIL: 2,
}


class CheckKind(StrEnum):
    """Why a check has the authority it has.

    DETERMINISTIC checks are facts — a signature either verifies or it does not.
    JUDGEMENT checks are thresholds and LLM findings, tunable in config.
    """

    DETERMINISTIC = "DETERMINISTIC"
    JUDGEMENT = "JUDGEMENT"


class ReasonCode(StrEnum):
    """Stable codes. The dashboard and the audit log render these directly."""

    # --- Deterministic: the mandate itself ---
    MANDATE_DIGEST_MISMATCH = "MANDATE_DIGEST_MISMATCH"
    MANDATE_SIGNATURE_INVALID = "MANDATE_SIGNATURE_INVALID"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_ALREADY_CONSUMED = "MANDATE_ALREADY_CONSUMED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    NONCE_REPLAYED = "NONCE_REPLAYED"

    # --- Deterministic: the charge against the mandate ---
    CHARGE_MANDATE_MISMATCH = "CHARGE_MANDATE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    CURRENCY_NOT_SETTLEABLE = "CURRENCY_NOT_SETTLEABLE"
    CHARGE_OVER_CAP = "CHARGE_OVER_CAP"

    # --- Deterministic: the counterparty ---
    MERCHANT_NOT_REGISTERED = "MERCHANT_NOT_REGISTERED"
    MERCHANT_INACTIVE = "MERCHANT_INACTIVE"
    PAYOUT_ADDRESS_MISMATCH = "PAYOUT_ADDRESS_MISMATCH"
    MERCHANT_BINDING_MISMATCH = "MERCHANT_BINDING_MISMATCH"
    BASKET_BINDING_MISMATCH = "BASKET_BINDING_MISMATCH"

    # --- Deterministic: the evidence ---
    EVALUATOR_NOT_REGISTERED = "EVALUATOR_NOT_REGISTERED"
    EVALUATOR_SIGNATURE_INVALID = "EVALUATOR_SIGNATURE_INVALID"
    EVALUATOR_SUBJECT_MISMATCH = "EVALUATOR_SUBJECT_MISMATCH"

    # --- Judgement: thresholds and LLM findings ---
    RISK_SCORE_CRITICAL = "RISK_SCORE_CRITICAL"
    RISK_SCORE_REVIEW_BAND = "RISK_SCORE_REVIEW_BAND"
    INTENT_MISMATCH_SUSPECTED = "INTENT_MISMATCH_SUSPECTED"
    INJECTION_SUSPECTED = "INJECTION_SUSPECTED"
    SUSPICIOUS_SELLER_PRICING = "SUSPICIOUS_SELLER_PRICING"


class CheckResult(BaseModel):
    """One check and what it concluded. PASS means the check was satisfied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: CheckKind
    decision: Decision
    reason: ReasonCode | None = None
    detail: str | None = None


class Verdict(BaseModel):
    """The Verifier's answer, with the full trace that produced it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Decision
    reason_codes: list[ReasonCode]
    checks: list[CheckResult]
    mandate_id: Hex32
    charge_id: Hex32
    risk_score: Annotated[int, Field(ge=MIN_RISK_SCORE, le=MAX_RISK_SCORE)]
    evaluated_at: Timestamp
    verifier_version: str
    config_version: str

    @model_validator(mode="before")
    @classmethod
    def _ignore_derived_fields(cls, data: Any) -> Any:
        """Drop `failed_deterministically` if a payload carries it.

        It is computed, so it is emitted but never accepted: a client asserting
        its own answer to "was this a fact or a threshold" is exactly the claim
        this system does not take on trust. Dropping rather than rejecting is
        what lets a Verdict round-trip through the settlement queue, which
        serialises to JSON and validates back.
        """
        if isinstance(data, dict) and "failed_deterministically" in data:
            return {k: v for k, v in data.items() if k != "failed_deterministically"}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed_deterministically(self) -> bool:
        """True when a fact, not a threshold, rejected this charge.

        This is the distinction the approval UI needs: a deterministic FAIL must
        never render an override button.

        **Computed, so that it crosses the wire.** It was a plain property, which
        meant it was absent from the JSON entirely — and a dashboard reading
        `undefined` gets a falsy value and renders the override button on a bad
        signature. Deriving it client-side instead would put this rule in two
        languages and let them drift. The server states it.
        """
        return any(
            check.decision is Decision.FAIL and check.kind is CheckKind.DETERMINISTIC
            for check in self.checks
        )
