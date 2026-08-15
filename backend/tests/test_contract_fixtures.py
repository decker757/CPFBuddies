import json
from pathlib import Path

from app.contracts import EvaluatorOutput

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_evaluator_fixtures_match_shared_contract() -> None:
    for fixture in FIXTURES.glob("evaluator_output.*.json"):
        payload = json.loads(fixture.read_text())
        assert EvaluatorOutput.model_validate(payload).model_dump(mode="json") == payload
