# Workstream B: agents and merchant surface

This package implements the merchant and agent side of the CPF Buddies trust rail. It is
intentionally independent of mandate issuance, verdict enforcement, and settlement.

## Components

- `app/contracts.py`: strict Pydantic integration contracts shared with other workstreams.
- `app/marketplace`: demo catalog, quote store, `GET /listings`, and x402-style `POST /purchase`.
- `app/agents/browser.py`: merchant client protocol, HTTP adapter, and deterministic selection.
- `app/agents/evaluator.py`: structured intent, cap, prompt-injection, price, and seller checks.
- `app/agents/bedrock.py`: optional Amazon Nova Lite structured-output evaluator adapter.
- `app/main.py`: FastAPI composition root.
- `contracts/`: generated OpenAPI and JSON Schema artifacts for cross-workstream integration.

Merchant payloads reject unknown fields and cap all text lengths. The Evaluator never executes
or interpolates listing text; it treats the title and description as untrusted data. The Browser
Agent's candidate remains attached to the merchant's original `quote_id` and `basket_hash` for
downstream verification.

## Run locally

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/uvicorn app.main:app --reload
```

OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

## Test

```bash
cd backend
.venv/bin/python -m pytest -q
```

Run all local quality gates:

```bash
cd backend
.venv/bin/ruff check app tests scripts
.venv/bin/ruff format --check app tests scripts
.venv/bin/mypy app
.venv/bin/coverage run -m pytest -q
.venv/bin/coverage report
```

Regenerate the checked-in integration contracts after changing a Pydantic model or route:

```bash
cd backend
.venv/bin/python -m scripts.export_contracts
```

Run the opt-in live evaluator matrix after setting `AWS_BEARER_TOKEN_BEDROCK` in the shell or in
the ignored `backend/.env` file:

```bash
cd backend
.venv/bin/python -m scripts.smoke_bedrock
```

The matrix exercises clean, injection, substitution, and low-price/new-seller listings. It makes
four Bedrock calls and fails if a score or reason code leaves its expected safety band.

## Integration boundaries

The Purchase Orchestrator can import `BrowserAgent` and `EvaluatorAgent`, or wrap them as
separately deployed services without changing their models. `ListingsClient` is a protocol, so
merchant discovery can later supply one HTTP client per registered merchant. `QuoteStore` is an
in-memory demo adapter and can be replaced by DynamoDB while retaining the public contract.

`POST /purchase` deliberately accepts the mandate credential as an opaque object: Workstream A
owns its schema and signature. The Verifier and settlement workstreams—not this stub merchant—own
mandate verification and payment-proof validation.

Marketplace errors use stable machine-readable values in FastAPI's `detail` field:
`unknown_quote` (404), `expired_quote` (410), `sku_not_in_quote` (400), and conflict errors
`out_of_stock`, `quote_already_consumed`, or `quote_integrity_failed` (409).

The Bedrock adapter uses `ap-southeast-1`, the APAC Amazon Nova Lite inference profile, and a
forced tool response through Bedrock Runtime's `Converse` API. Authentication comes from the
standard AWS credential chain or `AWS_BEARER_TOKEN_BEDROCK`; credentials must never be committed.
The local smoke script loads an ignored `backend/.env` file when present.
`AWS_REGION` and `BEDROCK_MODEL_ID` can override the APAC defaults for local or deployed runtimes.
