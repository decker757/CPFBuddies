# Graph Report - CPFBuddies  (2026-08-15)

## Corpus Check
- 38 files · ~11,984 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 293 nodes · 720 edges · 15 communities (10 shown, 5 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 115 edges (avg confidence: 0.52)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `70af8cd8`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- CLAUDE.md
- test_marketplace.py
- ListingsResponse
- ModelAssessment
- contracts.py
- EvaluatorAgent
- Workstream B: agents and merchant surface
- main.py
- test_marketplace_service.py
- README.md
- app/__init__.py
- marketplace/__init__.py
- cpf-buddies-backend
- scripts/__init__.py

## God Nodes (most connected - your core abstractions)
1. `EvaluatorAgent` - 37 edges
2. `ListingsResponse` - 37 edges
3. `Listing` - 29 edges
4. `ModelAssessment` - 24 edges
5. `MarketplaceService` - 24 edges
6. `BrowserAgent` - 20 edges
7. `BedrockEvaluatorModel` - 16 edges
8. `create_marketplace_router()` - 16 edges
9. `EvaluationModelError` - 15 edges
10. `StrictModel` - 15 edges

## Surprising Connections (you probably didn't know these)
- `BedrockEvaluatorModel` --uses--> `Listing`  [INFERRED]
  backend/app/agents/bedrock.py → backend/app/contracts.py
- `BrowserAgent` --uses--> `CandidateSelection`  [INFERRED]
  backend/app/agents/browser.py → backend/app/contracts.py
- `BrowserAgent` --uses--> `Listing`  [INFERRED]
  backend/app/agents/browser.py → backend/app/contracts.py
- `EvaluatorAgent` --uses--> `EvaluationModelError`  [INFERRED]
  backend/app/agents/evaluator.py → backend/app/agents/model.py
- `EvaluatorAgent` --uses--> `ModelAssessment`  [INFERRED]
  backend/app/agents/evaluator.py → backend/app/contracts.py

## Import Cycles
- None detected.

## Communities (15 total, 5 thin omitted)

### Community 0 - "CLAUDE.md"
Cohesion: 0.05
Nodes (35): 1. `GET /listings` (discovery), 2. `POST /purchase` (checkout), A. Mandate and Verifier (the core), Adapters, Agents (ours, internal), Async, Atomic (single responsibility), AWS (+27 more)

### Community 1 - "test_marketplace.py"
Cohesion: 0.43
Nodes (7): api_request(), test_listings_rejects_unsupported_currency(), test_listings_returns_quote_and_stable_basket_hash(), test_purchase_rejects_sku_not_in_original_quote(), test_purchase_rejects_unexpected_fields(), test_purchase_uses_quote_basket_hash_in_x402_terms_then_receipt(), Response

### Community 2 - "ListingsResponse"
Cohesion: 0.09
Nodes (35): AsyncClient, BrowserAgent, HttpListingsClient, ListingsClient, NoCandidateFound, Decimal, Protocol, HTTP adapter for any merchant implementing the two-endpoint contract. (+27 more)

### Community 3 - "ModelAssessment"
Cohesion: 0.09
Nodes (31): Any, BedrockEvaluatorModel, BedrockRuntimeClient, ModelSignals, BaseModel, Decimal, Protocol, Strict semantic signals returned by Nova before deterministic policy mapping. (+23 more)

### Community 4 - "contracts.py"
Cohesion: 0.10
Nodes (34): APIRouter, CandidateSelection, MarketplaceErrorResponse, Merchant, PaymentRequired, PaymentTerms, PurchaseReceipt, PurchaseRequest (+26 more)

### Community 5 - "EvaluatorAgent"
Cohesion: 0.10
Nodes (27): EvaluatorAgent, Decimal, Combines deterministic safety rules with an optional external model assessment., Internal agents. Their outputs are untrusted until verified downstream., EvaluationModel, Protocol, EvaluationSecurityEvent, LoggingSecurityEventSink (+19 more)

### Community 7 - "Workstream B: agents and merchant surface"
Cohesion: 0.33
Nodes (5): Components, Integration boundaries, Run locally, Test, Workstream B: agents and merchant surface

### Community 8 - "main.py"
Cohesion: 0.19
Nodes (8): build_marketplace_service(), create_app(), InMemoryQuoteRepository, datetime, Thread-safe demo adapter implementing the quote repository port., SecureQuoteIdGenerator, SystemClock, FastAPI

### Community 9 - "test_marketplace_service.py"
Cohesion: 0.22
Nodes (11): FixedClock, make_service(), purchase_request(), datetime, SequentialIds, test_empty_catalog_still_returns_a_bound_quote(), test_expired_quote_is_rejected(), test_old_expired_quotes_are_cleaned_up() (+3 more)

## Knowledge Gaps
- **36 isolated node(s):** `cpf-buddies-backend`, `Working in this repo`, `What we are building`, `Hard requirements`, `1. `GET /listings` (discovery)` (+31 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ListingsResponse` connect `ListingsResponse` to `main.py`, `test_marketplace_service.py`, `contracts.py`?**
  _High betweenness centrality (0.126) - this node is a cross-community bridge._
- **Why does `Listing` connect `EvaluatorAgent` to `ListingsResponse`, `ModelAssessment`, `contracts.py`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `EvaluatorAgent` connect `EvaluatorAgent` to `ListingsResponse`, `ModelAssessment`?**
  _High betweenness centrality (0.071) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `EvaluatorAgent` (e.g. with `EvaluationModel` and `EvaluationModelError`) actually correct?**
  _`EvaluatorAgent` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ListingsResponse` (e.g. with `BrowserAgent` and `HttpListingsClient`) actually correct?**
  _`ListingsResponse` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `Listing` (e.g. with `BedrockEvaluatorModel` and `BrowserAgent`) actually correct?**
  _`Listing` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `ModelAssessment` (e.g. with `BedrockEvaluatorModel` and `EvaluatorAgent`) actually correct?**
  _`ModelAssessment` has 8 INFERRED edges - model-reasoned connections that need verification._