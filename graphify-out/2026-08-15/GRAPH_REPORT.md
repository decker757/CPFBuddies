# Graph Report - CPFBuddies  (2026-08-15)

## Corpus Check
- 28 files · ~6,813 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 213 nodes · 428 edges · 17 communities (14 shown, 3 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 42 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `2bd46e32`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- CLAUDE.md
- Services
- Frontend
- AWS
- Mandate Service
- AgentRecord
- make_service
- evaluate
- config.py
- manual_test.sh
- README.md

## God Nodes (most connected - your core abstractions)
1. `MandateService` - 33 edges
2. `Mandate` - 29 edges
3. `create_mandate()` - 19 edges
4. `sign_authorization_request()` - 18 edges
5. `MandateRepository` - 16 edges
6. `InMemoryMandateRepository` - 15 edges
7. `AuthorizationResult` - 15 edges
8. `MandateStatus` - 14 edges
9. `MandateCreateRequest` - 13 edges
10. `AgentRecord` - 12 edges

## Surprising Connections (you probably didn't know these)
- `make_service()` --calls--> `AgentRecord`  [INFERRED]
  backend/tests/test_signature_verification.py → backend/app/agent_registry/models.py
- `AuthorizationResult` --uses--> `AgentRegistryRepository`  [INFERRED]
  backend/app/mandate/service.py → backend/app/agent_registry/repository.py
- `MandateService` --uses--> `AgentRegistryRepository`  [INFERRED]
  backend/app/mandate/service.py → backend/app/agent_registry/repository.py
- `AuthorizationResult` --uses--> `Mandate`  [INFERRED]
  backend/app/mandate/service.py → backend/app/mandate/models.py
- `MandateService` --uses--> `Mandate`  [INFERRED]
  backend/app/mandate/service.py → backend/app/mandate/models.py

## Import Cycles
- None detected.

## Communities (17 total, 3 thin omitted)

### Community 0 - "CLAUDE.md"
Cohesion: 0.08
Nodes (22): Adapters, Agent implementation, Async, Atomic (single responsibility), AWS, Build order, Composite (request/response orchestration), Core primitive: the mandate (+14 more)

### Community 1 - "Services"
Cohesion: 0.12
Nodes (28): MandateStatus, str, authorize_payment(), create_mandate(), get_agent_registry_repository(), get_mandate(), get_mandate_repository(), get_mandate_service() (+20 more)

### Community 2 - "Frontend"
Cohesion: 0.15
Nodes (26): create_mandate(), create_request(), make_account(), Account, datetime, sign_authorization_request(), test_authorize_nonce_replay_fails(), test_authorize_stale_timestamp_fails() (+18 more)

### Community 3 - "AWS"
Cohesion: 0.10
Nodes (7): Mandate, datetime, Decimal, Enum, InMemoryMandateRepository, MandateRepository, ABC

### Community 4 - "Mandate Service"
Cohesion: 0.07
Nodes (25): Authorize a payment, Create a mandate, Example Allowed Transaction, Example API Requests, Example Rejected Transaction, Future Avalanche Integration, Installation, Mandate Service (+17 more)

### Community 5 - "AgentRecord"
Cohesion: 0.21
Nodes (12): AgentRecord, AgentRegistryRepository, InMemoryAgentRegistryRepository, ABC, get_agent_registry_repository(), register_agent(), AgentRegisterRequest, AgentRegisterResponse (+4 more)

### Community 6 - "make_service"
Cohesion: 0.32
Nodes (14): build_canonical_authorization_payload(), format_amount(), Decimal, create_mandate(), make_account(), make_service(), Account, datetime (+6 more)

### Community 7 - "evaluate"
Cohesion: 0.43
Nodes (6): AuthorizationCharge, evaluate(), datetime, make_charge(), make_mandate(), test_evaluate_reason_codes()

## Knowledge Gaps
- **44 isolated node(s):** `Settings`, `manual_test.sh script`, `Working in this repo`, `What we are building`, `Hard requirements` (+39 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MandateService` connect `Services` to `AWS`, `AgentRecord`, `make_service`, `evaluate`?**
  _High betweenness centrality (0.167) - this node is a cross-community bridge._
- **Why does `reset_repository()` connect `AgentRecord` to `Services`, `Frontend`, `AWS`?**
  _High betweenness centrality (0.107) - this node is a cross-community bridge._
- **Why does `Mandate` connect `AWS` to `Services`, `make_service`, `evaluate`?**
  _High betweenness centrality (0.086) - this node is a cross-community bridge._
- **Are the 12 inferred relationships involving `MandateService` (e.g. with `AgentRegistryRepository` and `Mandate`) actually correct?**
  _`MandateService` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Mandate` (e.g. with `InMemoryMandateRepository` and `MandateRepository`) actually correct?**
  _`Mandate` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `MandateRepository` (e.g. with `Mandate` and `AuthorizationResult`) actually correct?**
  _`MandateRepository` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Settings`, `manual_test.sh script`, `Working in this repo` to the rest of the system?**
  _44 weakly-connected nodes found - possible documentation gaps or missing edges._