"""Export stable JSON Schema and OpenAPI artifacts for other workstreams."""

from __future__ import annotations

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

OUTPUT_DIR = Path(__file__).parents[1] / "contracts"
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


def write_json(path: Path, value: object) -> None:
    path.write_text(f"{json.dumps(value, indent=2, sort_keys=True)}\n")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    schemas = {model.__name__: model.model_json_schema() for model in MODELS}
    write_json(OUTPUT_DIR / "workstream-b.schemas.json", schemas)
    write_json(OUTPUT_DIR / "openapi.json", app.openapi())


if __name__ == "__main__":
    main()
