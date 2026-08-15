import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.agents.browser import BrowserAgent
from app.agents.evaluator import EvaluatorAgent
from app.contracts import ListingsResponse, xsgd
from app.integrity import calculate_basket_hash
from app.marketplace.catalog import CATALOG
from app.marketplace.service import MERCHANT


class ScenarioMerchant:
    async def fetch_listings(self, **kwargs) -> ListingsResponse:
        del kwargs
        items = list(CATALOG)
        return ListingsResponse(
            quote_id="q_scenario",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            merchant=MERCHANT,
            items=items,
            basket_hash=calculate_basket_hash(items),
        )


@pytest.mark.parametrize(
    ("sku", "expected_score", "expected_band"),
    [
        ("TB-SOFT-2PK", 1, "PASS"),
        ("TB-SUSPICIOUS", 5, "REVIEW"),
        ("TB-INJECTION", 10, "FAIL"),
    ],
)
def test_complete_browser_to_evaluator_scenarios(
    sku: str, expected_score: int, expected_band: str
) -> None:
    candidate = asyncio.run(
        BrowserAgent([ScenarioMerchant()]).find_candidate(
            intent="toothbrush under $5",
            max_price=xsgd("5"),
            preferred_sku=sku,
        )
    )
    output = EvaluatorAgent().evaluate(
        listing=candidate.listing,
        intent="toothbrush under $5",
        max_amount=xsgd("5"),
    )
    band = "PASS" if output.risk_score <= 3 else "REVIEW" if output.risk_score <= 7 else "FAIL"
    assert output.risk_score == expected_score
    assert band == expected_band
