# Graph Report - CPFBuddies  (2026-08-15)

## Corpus Check
- 88 files · ~32,963 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 803 nodes · 1748 edges · 55 communities (42 shown, 13 thin omitted)
- Extraction: 77% EXTRACTED · 23% INFERRED · 0% AMBIGUOUS · INFERRED: 401 edges (avg confidence: 0.61)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `70af8cd8`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- CLAUDE.md
- Services
- Frontend
- AWS
- Merchant integration contract
- Runtime flow
- ScenarioBuilder
- VerificationContext
- test_store_contract.py
- VerifierService
- README.md
- KillSwitchStore
- test_mandate_service.py
- test_money.py
- MandateRecord
- FakeKmsClient
- test_api.py
- DynamoMandateStore
- LocalSigner
- Mandate
- mandate_digest
- InMemoryMandateStore
- Currency
- .from_toml
- ReviewHold
- MandateNotFound
- MandateService
- AuditEntry
- to_bytes
- .deadline_for
- ._transition
- kms.py
- canonical_json
- test_contract_fixtures.py
- VerifierConfig
- InMemoryKillSwitchStore
- export.py
- create_demo_app
- clock.py
- signed_by
- create_tables
- CPFBuddies — TrustRail
- DynamoKillSwitchStore
- What each workstream needs
- new_hex32
- IllegalBinding
- .public_key
- __init__.py
- __init__.py
- __init__.py
- __init__.py
- __init__.py
- __init__.py
- __init__.py
- trustrail

## God Nodes (most connected - your core abstractions)
1. `ScenarioBuilder` - 81 edges
2. `MandateService` - 69 edges
3. `MandateRecord` - 53 edges
4. `VerificationContext` - 45 edges
5. `Money` - 41 edges
6. `VerifierService` - 41 edges
7. `Rejection` - 39 edges
8. `AuditEntry` - 35 edges
9. `VerifierConfig` - 33 edges
10. `Scenario` - 32 edges

## Surprising Connections (you probably didn't know these)
- `test_a_hold_starts_pending_and_records_who_resolved_it()` --calls--> `ReviewHold`  [INFERRED]
  tests/test_review_hold.py → src/trustrail/models/review.py
- `client()` --calls--> `create_app()`  [INFERRED]
  tests/test_api.py → src/trustrail/app.py
- `_ids()` --references--> `Scenario`  [EXTRACTED]
  tests/test_contract_fixtures.py → src/trustrail/contracts/scenarios.py
- `client()` --calls--> `demo_config()`  [INFERRED]
  tests/test_api.py → src/trustrail/contracts/scenarios.py
- `build()` --references--> `ScenarioBuilder`  [EXTRACTED]
  tests/conftest.py → src/trustrail/contracts/scenarios.py

## Import Cycles
- None detected.

## Communities (55 total, 13 thin omitted)

### Community 0 - "CLAUDE.md"
Cohesion: 0.11
Nodes (17): Adapters, Build order, Core primitive: the mandate, Demo flow, Frontend, Hard requirements, Offchain vs onchain split, Onchain (+9 more)

### Community 1 - "Services"
Cohesion: 0.40
Nodes (5): Agents (ours, internal), Async, Atomic (single responsibility), Composite (request/response orchestration), Services

### Community 2 - "Frontend"
Cohesion: 0.33
Nodes (6): A. Mandate and Verifier (the core), B. Agents and merchant surface, C. Chain and settlement, D. Platform, frontend, and pitch, Integration points, Workstream split

### Community 4 - "Merchant integration contract"
Cohesion: 0.67
Nodes (3): 1. `GET /listings` (discovery), 2. `POST /purchase` (checkout), Merchant integration contract

### Community 6 - "ScenarioBuilder"
Cohesion: 0.07
Nodes (56): label_to_address(), label_to_hash(), Fixed keys and identifiers for the demo fixtures.  These are hardcoded so the fi, A stable 32-byte value from a readable label.      Fixtures full of `0xaaaa...`, A stable address from a readable label., build_scenarios(), _counterparty_scenarios(), _forgery_scenarios() (+48 more)

### Community 7 - "VerificationContext"
Cohesion: 0.06
Nodes (59): Check, AgentRole, StrEnum, CheckKind, CheckResult, BaseModel, StrEnum, The verdict: three outcomes, and an honest account of how we got there.  The spl (+51 more)

### Community 8 - "test_store_contract.py"
Cohesion: 0.08
Nodes (42): Self, The strictest decision in the list; PASS when the list is empty., _entry(), held_verdict(), _hold(), Any, datetime, One suite, run against both store implementations.  The in-memory stores are not (+34 more)

### Community 9 - "VerifierService"
Cohesion: 0.12
Nodes (23): Decision, Decides whether a charge may settle against a mandate., VerifierService, verifier(), Boundary cases, where a check either holds exactly or does not hold at all.  The, Thresholds are config. This is the test that proves it., Addresses are normalised at parse time, so casing is never a mismatch., The basket may match while the price has been raised underneath it. (+15 more)

### Community 11 - "KillSwitchStore"
Cohesion: 0.10
Nodes (16): Protocol, Wall-clock UTC. The production implementation., SystemClock, AuditLog, Clock, KillSwitchStore, datetime, The seams between this package and the outside world.  Five protocols, one file, (+8 more)

### Community 12 - "test_mandate_service.py"
Cohesion: 0.12
Nodes (23): MandateStatusConflict, A conditional write lost a race, or the transition was illegal.      This is wha, _mint(), The Mandate Service: what it will and will not let you do.  The interesting test, The Verifier's replay check reads this index., Two settlement workers racing on one mandate. Only one may spend it., The same record under a different id, keeping the original nonce., The human approved a budget and an intent, not a SKU. (+15 more)

### Community 13 - "test_money.py"
Cohesion: 0.12
Nodes (22): Self, Build an amount from smallest units, e.g. 4_200_000 XSGD -> "4.20"., Money must be exact, because the cap is a FAIL nobody can override.  Anything th, A cap that could be mutated after signing would not be a cap., Fixtures stay byte-stable and equal amounts are literally equal., The EIP-712 encoder packs minor units into a 32-byte word, and so does     the c, 0.1 + 0.2 has no place anywhere near a payment cap., Rounding here would let a charge hide just above the cap. (+14 more)

### Community 14 - "MandateRecord"
Cohesion: 0.11
Nodes (13): MandateRecord, MandateStatus, StrEnum, A mandate as it is stored: the signed struct plus its lifecycle., A copy at the next lifecycle step. Records are never mutated., Lifecycle of a mandate. Only MINTED and BOUND can still spend., MandateStore, Persistence for mandates, with the conditional writes that make     one-time con (+5 more)

### Community 15 - "FakeKmsClient"
Cohesion: 0.15
Nodes (17): KmsSigner, Any, Signs mandate digests with an asymmetric KMS key.      The key must be `ECC_SECG, The address derived from the KMS public key, fetched once., _der_integer(), FakeKmsClient, Any, The KMS signing gotcha, tested without an AWS account.  CLAUDE.md calls this out (+9 more)

### Community 16 - "test_api.py"
Cohesion: 0.18
Nodes (21): TestClient, client(), _mint(), The HTTP surface.  Thin routers over the services, so these tests check the wiri, The HTTP shape of one-time consumption., Strict schemas, unexpected fields refused. Nothing gets in loosely typed., A rejected charge is a verdict to render, not an exception to swallow., An app signing with the same issuer key the fixtures were signed with. (+13 more)

### Community 17 - "DynamoMandateStore"
Cohesion: 0.13
Nodes (11): ClientError, DynamoMandateStore, _explain_failed_creation(), _failed(), _mandate_item(), _nonce_item(), Any, Exception (+3 more)

### Community 18 - "LocalSigner"
Cohesion: 0.10
Nodes (14): address_of(), Sign a 32-byte digest, returning `0x` + r || s || v with v in {27, 28}., The lowercase address that `sign_digest` will produce signatures for., sign_digest(), LocalSigner, A signer backed by a local private key.  For tests, for the offline demo path, a, Signs with an in-process secp256k1 key., A fresh random key. Tests and local runs only. (+6 more)

### Community 19 - "Mandate"
Cohesion: 0.13
Nodes (14): An honestly hashed mandate carrying somebody else's signature.          This is, timedelta, Issue a mandate for a budget and an intent.          No merchant and no basket:, Mandate, MandateBinding, BaseModel, The mandate: a signed credential scoped to one purchase intent.  A mandate is mi, The struct that gets hashed and signed. Field order matches EIP-712. (+6 more)

### Community 20 - "mandate_digest"
Cohesion: 0.16
Nodes (17): Eip712Domain, mandate_digest(), BaseModel, Domain separator inputs.      `verifying_contract` stays at the zero address unt, The 32-byte digest signed by the issuer and recomputed by the contract., The mandate digest, pinned.  Track C's MandateRegistry contract has to recompute, Every field is actually covered by the signature, not just carried near it., S$5 and US$5 are different approvals and must not share a digest. (+9 more)

### Community 21 - "InMemoryMandateStore"
Cohesion: 0.16
Nodes (9): MandateAlreadyExists, NonceAlreadyClaimed, A nonce was reused across two mandates. Breaks one-time consumption., InMemoryMandateStore, InMemoryReviewHoldStore, datetime, In-memory stores: the default for tests and the offline demo path.  These are no, Held charges, filtered by deadline on read rather than swept. (+1 more)

### Community 22 - "Currency"
Cohesion: 0.21
Nodes (15): Decimal, Currency, _format_amount(), _parse_decimal(), Any, StrEnum, Money as an exact value object.  The mandate cap is the load-bearing arithmetic, Reject precision the currency cannot represent, rather than rounding it.      Si (+7 more)

### Community 23 - ".from_toml"
Cohesion: 0.12
Nodes (15): MonkeyPatch, Path, Self, Load settings from a TOML file, letting the environment override them., The shipped config file must load and mean what the code assumes., A track rule, so it belongs in a test rather than in a comment., CLAUDE.md: deploy and test on Fuji first, touch mainnet only after., Deployments set the KMS issuer address without editing a committed file. (+7 more)

### Community 24 - "ReviewHold"
Cohesion: 0.15
Nodes (12): BaseModel, Self, StrEnum, A paused charge, its evidence, and the moment it stops waiting., ReviewHold, ReviewOutcome, Charges paused for a human, each with a deadline it cannot outlive., Holds still awaiting a human and not yet past their deadline.          Anything (+4 more)

### Community 25 - "MandateNotFound"
Cohesion: 0.18
Nodes (15): MandateNotFound, Exception, Errors raised by the Mandate Service and its stores.  Note what is *not* here: t, Base class for everything this package raises deliberately., TrustRailError, BindRequest, build_router(), KillSwitchRequest (+7 more)

### Community 26 - "MandateService"
Cohesion: 0.13
Nodes (9): MandateService, The Mandate Service: mint, bind, revoke, consume, kill switch.  This is the only, The panic button: stop settlement for everyone., Stop settlement for one buyer., Everything that has happened to one mandate, oldest first., Issues and manages the credentials that authorise spending., The address the Verifier must be configured to trust., A FAIL nobody can point to afterwards is not an auditable system. (+1 more)

### Community 27 - "AuditEntry"
Cohesion: 0.15
Nodes (10): AuditEntry, BaseModel, One immutable record. Ordered by `occurred_at` within a mandate., Entries for one mandate, oldest first., DynamoAuditLog, Append-only. Sorted by time within a mandate, so reads are already ordered., InMemoryAuditLog, Every entry, oldest first. Backs the demo's decision dashboard. (+2 more)

### Community 28 - "to_bytes"
Cohesion: 0.23
Nodes (13): Decode a 0x-prefixed hex string into raw bytes., to_bytes(), hash_bytes(), keccak256, returned as a 0x-prefixed lowercase hash., _encode_address(), _encode_bytes32(), _encode_string(), _encode_uint() (+5 more)

### Community 29 - ".deadline_for"
Cohesion: 0.15
Nodes (12): datetime, timedelta, A charge held for a human decision.  CLAUDE.md is explicit that REVIEW needs a h, The earlier of the review window and the mandate's own expiry.          Taking t, Review deadlines.  CLAUDE.md is explicit: REVIEW needs a hold state and a timeou, A human cannot be given longer to approve than the mandate has to live., Naive datetimes would make every expiry comparison a coin flip., test_a_hold_never_outlives_its_mandate() (+4 more)

### Community 30 - "._transition"
Cohesion: 0.16
Nodes (6): Commit a mandate to one merchant and one basket, and re-sign it.          This i, Kill one mandate. Revocation is a deterministic FAIL thereafter., Spend a mandate, exactly once.          This is where replay protection actually, AuditEventType, StrEnum, The audit trail.  Append-only, keyed by mandate. Every state change and every ve

### Community 31 - "kms.py"
Cohesion: 0.19
Nodes (12): address_from_public_key(), _normalise_s(), _pack(), _parse_der_signature(), A signer whose private key lives in KMS and never leaves it.  KMS will happily s, Fold `s` into the low half of the curve order, as EIP-2 requires., Find the recovery id that reproduces our own address, and pack it in., The Ethereum address for a KMS `GetPublicKey` DER blob.      Useful for the depl (+4 more)

### Community 32 - "canonical_json"
Cohesion: 0.17
Nodes (8): canonical_json(), BaseModel, One byte-stable encoding of a model, used wherever bytes must be reproducible., JSON with sorted keys and no whitespace, encoded as UTF-8., The Evaluator's output digest.  Deliberately *not* EIP-712. This payload is evid, Verifier configuration.  Thresholds are config, not code — they get tuned during, A short fingerprint of these settings, stamped onto every verdict., _set_in_env()

### Community 33 - "test_contract_fixtures.py"
Cohesion: 0.17
Nodes (11): _ids(), The golden corpus.  Every committed fixture must still produce the verdict it cl, Guards against editing a scenario and forgetting to re-export., A deleted scenario must not leave its fixture behind., Nothing is rejected without a check and a reason code to point at., The approval UI reads this flag to decide whether to offer a button., test_committed_fixture_matches_generated_scenario(), test_deterministic_failures_are_flagged_as_non_overridable() (+3 more)

### Community 34 - "VerifierConfig"
Cohesion: 0.20
Nodes (10): BaseSettings, demo_config(), The Verifier settings the fixtures were generated under., Everything the decision function needs beyond the request itself., VerifierConfig, config(), A silent policy change would make two verdicts incomparable., An empty REVIEW band would mean no human is ever in the loop. (+2 more)

### Community 35 - "InMemoryKillSwitchStore"
Cohesion: 0.22
Nodes (6): FixtureRequest, InMemoryKillSwitchStore, The global switch and the per-principal switches. Either one halts., _dynamo_stores(), Every store, from whichever backend this parametrisation asked for., stores()

### Community 36 - "export.py"
Cohesion: 0.36
Nodes (10): _default_root(), export(), Path, Write the JSON Schemas and golden fixtures to `contracts/`.  Run `python -m trus, Regenerate `contracts/` in place., A table of what each fixture proves, generated so it cannot go stale., _write_fixtures(), _write_index() (+2 more)

### Community 37 - "create_demo_app"
Cohesion: 0.27
Nodes (8): FastAPI, create_app(), create_demo_app(), Wires the services into one FastAPI app.  Two factories. `create_app` takes serv, The whole rail, offline: a local issuer key and in-memory stores.      The Verif, build_router(), APIRouter, HTTP surface for the Verifier Service.  One endpoint, and it takes everything it

### Community 38 - "clock.py"
Cohesion: 0.36
Nodes (5): FrozenClock, datetime, Time, injected rather than read.  Expiry is a deterministic FAIL, so "what time, A clock that only moves when a test moves it., _require_aware()

### Community 39 - "signed_by"
Cohesion: 0.25
Nodes (8): secp256k1 primitives, in the shape Avalanche expects.  A signature on the wire i, Recover the signing address, or None if the signature is unusable.      Returnin, True when `signature` over `digest` was produced by `expected_address`., recover_address(), signed_by(), A bound mandate is a different credential, and says so cryptographically., test_a_minted_mandate_carries_a_verifiable_issuer_signature(), test_binding_re_signs_so_the_digest_changes()

### Community 40 - "create_tables"
Cohesion: 0.44
Nodes (8): _create(), create_tables(), _enable_pitr(), _enable_ttl(), Any, DynamoDB table definitions, as code.  Kept next to the stores that use them so t, Create every table this package needs, if it does not already exist., _table_definitions()

### Community 41 - "CPFBuddies — TrustRail"
Cohesion: 0.29
Nodes (7): CPFBuddies — TrustRail, Deploying, Known gaps, Quick start, Regenerating the contracts, The three ideas worth knowing, What is here

### Community 43 - "What each workstream needs"
Cohesion: 0.33
Nodes (6): B — agents and merchant surface, C — chain and settlement, Contracts, D — platform and frontend, Two deliberate deviations from CLAUDE.md, What each workstream needs

### Community 44 - "new_hex32"
Cohesion: 0.33
Nodes (4): Put a Verifier decision into the audit trail.          The Verifier cannot do th, new_hex32(), Shared scalar types for the wire contract.  Every hex value in this system is no, Generate a random 32-byte identifier (mandate id, nonce).

### Community 45 - "IllegalBinding"
Cohesion: 0.40
Nodes (5): IllegalBinding, A `bind` tried to do something other than fill in an empty field.      Binding m, Otherwise a second approval could redirect an already-approved purchase., test_a_mandate_cannot_be_bound_twice(), test_a_revoked_mandate_cannot_be_bound()

## Knowledge Gaps
- **42 isolated node(s):** `trustrail`, `Working in this repo`, `What we are building`, `Hard requirements`, `1. `GET /listings` (discovery)` (+37 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ScenarioBuilder` connect `ScenarioBuilder` to `VerifierConfig`, `VerificationContext`, `test_store_contract.py`, `VerifierService`, `MandateRecord`, `FakeKmsClient`, `test_api.py`, `LocalSigner`, `Mandate`, `mandate_digest`, `Currency`, `to_bytes`?**
  _High betweenness centrality (0.185) - this node is a cross-community bridge._
- **Why does `MandateService` connect `MandateService` to `create_demo_app`, `ScenarioBuilder`, `VerificationContext`, `signed_by`, `KillSwitchStore`, `test_mandate_service.py`, `IllegalBinding`, `MandateRecord`, `new_hex32`, `test_api.py`, `LocalSigner`, `Mandate`, `mandate_digest`, `MandateNotFound`, `AuditEntry`, `._transition`?**
  _High betweenness centrality (0.168) - this node is a cross-community bridge._
- **Why does `Money` connect `ScenarioBuilder` to `VerifierService`, `test_money.py`, `MandateRecord`, `Mandate`, `mandate_digest`, `Currency`, `MandateNotFound`, `MandateService`?**
  _High betweenness centrality (0.125) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `ScenarioBuilder` (e.g. with `Charge` and `EvaluationSubject`) actually correct?**
  _`ScenarioBuilder` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `MandateService` (e.g. with `BindRequest` and `KillSwitchRequest`) actually correct?**
  _`MandateService` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 20 inferred relationships involving `MandateRecord` (e.g. with `BindRequest` and `KillSwitchRequest`) actually correct?**
  _`MandateRecord` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `VerificationContext` (e.g. with `Charge` and `EvaluatorOutput`) actually correct?**
  _`VerificationContext` has 11 INFERRED edges - model-reasoned connections that need verification._