from decimal import Decimal

from app.agents.evaluator import EvaluatorAgent
from app.agents.security import EvaluationSecurityEvent
from app.contracts import Listing, xsgd
from app.marketplace.catalog import CATALOG


def listing(sku: str):
    return next(item for item in CATALOG if item.sku == sku)


def test_clean_listing_is_low_risk() -> None:
    output = EvaluatorAgent().evaluate(
        listing=listing("TB-SOFT-2PK"), intent="toothbrush under $5", max_amount=xsgd("5")
    )
    assert output.risk_score == 1
    assert output.intent_match is True
    assert output.price_within_cap is True
    assert output.injection_detected is False
    assert output.reasons == []


def test_poisoned_listing_is_high_risk_with_structured_reason() -> None:
    output = EvaluatorAgent().evaluate(
        listing=listing("TB-INJECTION"), intent="toothbrush under $5", max_amount=xsgd("5")
    )
    assert output.risk_score == 10
    assert output.injection_detected is True
    assert {reason.code for reason in output.reasons} == {"PROMPT_INJECTION"}


def test_substitution_does_not_match_intent() -> None:
    output = EvaluatorAgent().evaluate(
        listing=listing("GIFT-SUBSTITUTE"), intent="toothbrush under $5", max_amount=xsgd("5")
    )
    assert output.risk_score == 8
    assert output.intent_match is False
    assert {reason.code for reason in output.reasons} == {"INTENT_MISMATCH"}


def test_low_price_new_seller_routes_to_review_band() -> None:
    output = EvaluatorAgent().evaluate(
        listing=listing("TB-SUSPICIOUS"), intent="toothbrush under $5", max_amount=xsgd("5")
    )
    assert output.risk_score == 5
    assert {reason.code for reason in output.reasons} == {
        "SUSPICIOUSLY_LOW_PRICE",
        "NEW_OR_UNRATED_SELLER",
    }


def test_merchant_cannot_bypass_cap_by_ignoring_discovery_filter() -> None:
    expensive = Listing(
        **listing("TB-SOFT-2PK").model_dump(exclude={"price"}),
        price=xsgd("6"),
    )
    output = EvaluatorAgent().evaluate(
        listing=expensive, intent="toothbrush under $5", max_amount=xsgd("5")
    )
    assert output.risk_score == 9
    assert output.price_within_cap is False
    assert {reason.code for reason in output.reasons} == {"PRICE_OVER_CAP"}


def test_security_event_contains_metadata_but_not_untrusted_text() -> None:
    class RecordingSink:
        def __init__(self) -> None:
            self.events: list[EvaluationSecurityEvent] = []

        def emit(self, event: EvaluationSecurityEvent) -> None:
            self.events.append(event)

    sink = RecordingSink()
    poisoned = listing("TB-INJECTION")
    EvaluatorAgent(sink).evaluate(
        listing=poisoned, intent="toothbrush under $5", max_amount=xsgd("5")
    )
    assert len(sink.events) == 1
    serialized = repr(sink.events[0])
    assert poisoned.title not in serialized
    assert poisoned.description not in serialized
    assert sink.events[0].reason_codes == ("PROMPT_INJECTION",)
