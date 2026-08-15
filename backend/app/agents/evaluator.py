from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal

from app.agents.model import EvaluationModel, EvaluationModelError
from app.agents.security import (
    EvaluationSecurityEvent,
    LoggingSecurityEventSink,
    SecurityEventSink,
)
from app.contracts import (
    EvaluatorOutput,
    Listing,
    ModelAssessment,
    RiskReason,
    RiskReasonCode,
)

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "buy",
    "for",
    "get",
    "me",
    "of",
    "please",
    "the",
    "to",
    "under",
    "with",
}
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"override\s+(the\s+)?(user'?s?\s+)?(budget|limit|mandate)", re.IGNORECASE),
    re.compile(r"(system|developer)\s+(message|prompt)", re.IGNORECASE),
    re.compile(r"conceal\s+(this|the)\s+instruction", re.IGNORECASE),
    re.compile(r"do\s+not\s+(tell|inform)\s+the\s+user", re.IGNORECASE),
)
_REASON_DETAILS = {
    RiskReasonCode.INTENT_MISMATCH: "Listing does not match the purchase intent.",
    RiskReasonCode.PRICE_OVER_CAP: "Listing price exceeds the mandate cap.",
    RiskReasonCode.PROMPT_INJECTION: "Listing contains instructions directed at an agent.",
    RiskReasonCode.PRODUCT_SUBSTITUTION: "Listing appears to substitute another product.",
    RiskReasonCode.SUSPICIOUSLY_LOW_PRICE: "Price is at most 20% of the approved budget.",
    RiskReasonCode.NEW_OR_UNRATED_SELLER: "Seller is new or has fewer than five ratings.",
    RiskReasonCode.EVALUATOR_UNAVAILABLE: (
        "Model evaluator was unavailable or returned invalid output."
    ),
}


class EvaluatorAgent:
    """Combines deterministic safety rules with an optional external model assessment."""

    rules_evaluator_id = "evaluator-rules-v1"
    hybrid_evaluator_id = "evaluator-hybrid-nova-v1"

    def __init__(
        self,
        security_events: SecurityEventSink | None = None,
        model: EvaluationModel | None = None,
    ) -> None:
        self._security_events = security_events or LoggingSecurityEventSink()
        self._model = model

    def evaluate(self, *, listing: Listing, intent: str, max_amount: Decimal) -> EvaluatorOutput:
        output = self._evaluate_rules(listing=listing, intent=intent, max_amount=max_amount)
        if self._model is not None:
            try:
                assessment = self._model.assess(
                    listing=listing,
                    intent=intent,
                    max_amount=max_amount,
                )
                output = self._merge(output, assessment)
            except EvaluationModelError:
                output = self._model_unavailable(output)
        self._emit_security_event(listing, output)
        return output

    def _evaluate_rules(
        self, *, listing: Listing, intent: str, max_amount: Decimal
    ) -> EvaluatorOutput:
        reason_codes: list[RiskReasonCode] = []
        intent_match = self._matches_intent(listing, intent)
        price_within_cap = listing.price.amount <= max_amount
        injection_detected = self._has_injection(listing)

        if not intent_match:
            reason_codes.append(RiskReasonCode.INTENT_MISMATCH)
        if not price_within_cap:
            reason_codes.append(RiskReasonCode.PRICE_OVER_CAP)
        if injection_detected:
            reason_codes.append(RiskReasonCode.PROMPT_INJECTION)

        suspicious_price = listing.price.amount <= max_amount * Decimal("0.20")
        new_or_unrated = listing.seller_account_age_days < 30 or listing.seller_rating_count < 5
        if suspicious_price:
            reason_codes.append(RiskReasonCode.SUSPICIOUSLY_LOW_PRICE)
        if new_or_unrated:
            reason_codes.append(RiskReasonCode.NEW_OR_UNRATED_SELLER)

        risk_score = self._score(
            intent_match=intent_match,
            price_within_cap=price_within_cap,
            injection_detected=injection_detected,
            suspicious_price=suspicious_price,
            new_or_unrated=new_or_unrated,
        )
        return EvaluatorOutput(
            evaluator_id=self.rules_evaluator_id,
            risk_score=risk_score,
            intent_match=intent_match,
            price_within_cap=price_within_cap,
            injection_detected=injection_detected,
            reasons=self._reasons(reason_codes),
        )

    def _merge(self, rules: EvaluatorOutput, assessment: ModelAssessment) -> EvaluatorOutput:
        reason_codes = {reason.code for reason in rules.reasons}
        reason_codes.update(RiskReasonCode(code) for code in assessment.reason_codes)

        intent_match = (
            rules.intent_match and assessment.intent_match and not assessment.substitution_suspected
        )
        injection_detected = rules.injection_detected or assessment.injection_detected
        if not intent_match:
            reason_codes.add(RiskReasonCode.INTENT_MISMATCH)
        if injection_detected:
            reason_codes.add(RiskReasonCode.PROMPT_INJECTION)
        if assessment.substitution_suspected:
            reason_codes.add(RiskReasonCode.PRODUCT_SUBSTITUTION)

        risk_score = max(rules.risk_score, assessment.risk_score)
        if injection_detected:
            risk_score = 10
        elif not intent_match or assessment.substitution_suspected:
            risk_score = max(risk_score, 4)

        return EvaluatorOutput(
            evaluator_id=self.hybrid_evaluator_id,
            risk_score=risk_score,
            intent_match=intent_match,
            price_within_cap=rules.price_within_cap,
            injection_detected=injection_detected,
            reasons=self._reasons(reason_codes),
        )

    def _model_unavailable(self, rules: EvaluatorOutput) -> EvaluatorOutput:
        reason_codes = {reason.code for reason in rules.reasons}
        reason_codes.add(RiskReasonCode.EVALUATOR_UNAVAILABLE)
        return rules.model_copy(
            update={
                "evaluator_id": self.hybrid_evaluator_id,
                "risk_score": max(rules.risk_score, 7),
                "reasons": self._reasons(reason_codes),
            }
        )

    def _emit_security_event(self, listing: Listing, output: EvaluatorOutput) -> None:
        if not output.reasons:
            return
        self._security_events.emit(
            EvaluationSecurityEvent(
                event_type="listing_evaluation_signal",
                evaluator_id=output.evaluator_id,
                sku=listing.sku,
                seller_id=listing.seller_id,
                risk_score=output.risk_score,
                reason_codes=tuple(reason.code.value for reason in output.reasons),
            )
        )

    @staticmethod
    def _reasons(reason_codes: Iterable[RiskReasonCode]) -> list[RiskReason]:
        codes = sorted(set(reason_codes), key=lambda code: code.value)
        return [RiskReason(code=code, detail=_REASON_DETAILS[code]) for code in codes]

    @staticmethod
    def _matches_intent(listing: Listing, intent: str) -> bool:
        intent_tokens = set(_TOKEN.findall(intent.casefold())) - _STOP_WORDS
        listing_tokens = set(_TOKEN.findall(listing.title.casefold()))
        return bool(intent_tokens) and bool(intent_tokens & listing_tokens)

    @staticmethod
    def _has_injection(listing: Listing) -> bool:
        untrusted_text = f"{listing.title}\n{listing.description}"
        return any(pattern.search(untrusted_text) for pattern in _INJECTION_PATTERNS)

    @staticmethod
    def _score(
        *,
        intent_match: bool,
        price_within_cap: bool,
        injection_detected: bool,
        suspicious_price: bool,
        new_or_unrated: bool,
    ) -> int:
        if injection_detected:
            return 10
        if not price_within_cap:
            return 9
        score = 1
        if not intent_match:
            score += 7
        if suspicious_price:
            score += 2
        if new_or_unrated:
            score += 2
        return min(score, 10)
