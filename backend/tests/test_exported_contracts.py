import json
from pathlib import Path

from app.contracts import (
    CandidateSelection,
    EvaluatorOutput,
    Listing,
    ListingsResponse,
    MarketplaceErrorResponse,
    ModelAssessment,
    PaymentRequired,
    PaymentTerms,
    PurchaseReceipt,
    PurchaseRequest,
)
from app.main import app

CONTRACTS = Path(__file__).parents[1] / "contracts"
MODELS = (
    Listing,
    ListingsResponse,
    MarketplaceErrorResponse,
    ModelAssessment,
    PurchaseRequest,
    PaymentTerms,
    PaymentRequired,
    PurchaseReceipt,
    CandidateSelection,
    EvaluatorOutput,
)


def test_exported_json_schemas_are_current() -> None:
    exported = json.loads((CONTRACTS / "workstream-b.schemas.json").read_text())
    expected = {model.__name__: model.model_json_schema() for model in MODELS}
    assert exported == expected


def test_exported_openapi_document_is_current() -> None:
    exported = json.loads((CONTRACTS / "openapi.json").read_text())
    assert exported == app.openapi()
