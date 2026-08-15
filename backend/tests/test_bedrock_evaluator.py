import json
from decimal import Decimal

import pytest

from app.agents.bedrock import (
    MODEL_ASSESSMENT_SCHEMA,
    SYSTEM_PROMPT,
    TOOL_NAME,
    BedrockEvaluatorModel,
)
from app.agents.evaluator import EvaluatorAgent
from app.agents.model import EvaluationModelError
from app.contracts import ModelAssessment
from app.marketplace.catalog import CATALOG


class FakeBedrockClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.request = None

    def converse(self, **kwargs):
        self.request = kwargs
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "test-tool-use",
                                "name": TOOL_NAME,
                                "input": self.payload,
                            }
                        }
                    ],
                }
            }
        }


class FakeEvaluationModel:
    model_id = "fake-model"

    def __init__(self, assessment: ModelAssessment | None = None, fail: bool = False) -> None:
        self.assessment = assessment
        self.fail = fail

    def assess(self, **kwargs) -> ModelAssessment:
        del kwargs
        if self.fail:
            raise EvaluationModelError("simulated outage")
        assert self.assessment is not None
        return self.assessment


def clean_assessment() -> dict:
    return {
        "intent_match": True,
        "injection_detected": False,
        "substitution_suspected": False,
    }


def test_bedrock_adapter_uses_structured_output_and_untrusted_data_boundary() -> None:
    client = FakeBedrockClient(clean_assessment())
    listing = CATALOG[1]
    output = BedrockEvaluatorModel(client).assess(
        listing=listing,
        intent="toothbrush under $5",
        max_amount=Decimal("5"),
    )

    assert output == ModelAssessment(
        intent_match=True,
        injection_detected=False,
        substitution_suspected=False,
        risk_score=1,
        reason_codes=[],
    )
    assert client.request is not None
    assert listing.description not in SYSTEM_PROMPT
    message_payload = json.loads(client.request["messages"][0]["content"][0]["text"])
    assert message_payload["untrusted_listing"]["description"] == listing.description
    tool_config = client.request["toolConfig"]
    configured_schema = tool_config["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert configured_schema == MODEL_ASSESSMENT_SCHEMA
    assert tool_config["toolChoice"] == {"tool": {"name": TOOL_NAME}}
    assert "additionalProperties" not in configured_schema
    assert set(configured_schema["properties"]) == {
        "intent_match",
        "injection_detected",
        "substitution_suspected",
    }


def test_bedrock_adapter_maps_model_signals_to_deterministic_policy() -> None:
    client = FakeBedrockClient(
        {
            "intent_match": False,
            "injection_detected": True,
            "substitution_suspected": True,
        }
    )

    output = BedrockEvaluatorModel(client).assess(
        listing=CATALOG[1], intent="toothbrush", max_amount=Decimal("5")
    )

    assert output.risk_score == 9
    assert output.reason_codes == [
        "INTENT_MISMATCH",
        "PROMPT_INJECTION",
        "PRODUCT_SUBSTITUTION",
    ]


def test_bedrock_adapter_rejects_invalid_model_response() -> None:
    client = FakeBedrockClient({"intent_match": True})
    with pytest.raises(EvaluationModelError):
        BedrockEvaluatorModel(client).assess(
            listing=CATALOG[0], intent="toothbrush", max_amount=Decimal("5")
        )


def test_bedrock_adapter_reads_region_and_model_from_environment(monkeypatch) -> None:
    captured = {}
    client = FakeBedrockClient(clean_assessment())

    def fake_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return client

    monkeypatch.setenv("AWS_REGION", "ap-southeast-3")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
    monkeypatch.setattr("app.agents.bedrock.boto3.client", fake_client)

    model = BedrockEvaluatorModel.from_environment()

    assert model.model_id == "amazon.nova-lite-v1:0"
    assert captured["service_name"] == "bedrock-runtime"
    assert captured["region_name"] == "ap-southeast-3"


def test_hybrid_evaluator_conservatively_merges_model_injection_signal() -> None:
    assessment = ModelAssessment(
        intent_match=True,
        injection_detected=True,
        substitution_suspected=False,
        risk_score=8,
        reason_codes=["PROMPT_INJECTION"],
    )
    output = EvaluatorAgent(model=FakeEvaluationModel(assessment)).evaluate(
        listing=CATALOG[0], intent="toothbrush under $5", max_amount=Decimal("5")
    )
    assert output.evaluator_id == "evaluator-hybrid-nova-v1"
    assert output.risk_score == 10
    assert output.injection_detected is True
    assert {reason.code for reason in output.reasons} == {"PROMPT_INJECTION"}


def test_hybrid_evaluator_routes_model_outage_to_review() -> None:
    output = EvaluatorAgent(model=FakeEvaluationModel(fail=True)).evaluate(
        listing=CATALOG[0], intent="toothbrush under $5", max_amount=Decimal("5")
    )
    assert output.risk_score == 7
    assert {reason.code for reason in output.reasons} == {"EVALUATOR_UNAVAILABLE"}
