# CLAUDE.md

Context for AI coding agents working on this repo. Read this before writing code.

## Working in this repo

Read this before exploring the codebase.

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and
cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json
  exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"`
  for focused concepts. These return a scoped subgraph, usually much smaller than
  GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when
  query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- Service boundaries in this repo are load-bearing (see Services below). Before adding a call
  between services, run `graphify path "<A>" "<B>"` to check you are not creating a cycle or
  bypassing the Verifier.

## What we are building

A trust rail for agent payments. The layer that lets an AI agent spend real money on someone's
behalf without the human having to trust the agent.

Core claim: **we do not trust the agent, we trust the rail.** Enforcement happens outside the
agent, at settlement, and is publicly verifiable onchain.

Hackathon track: Agentic Payments Infrastructure.
Also targeting: Avalanche "Best Use of x402", AWS "Best Architected", StraitsX "Real-World Impact".

## Hard requirements

- All settlement MUST use $XSGD on Avalanche C-Chain Mainnet. Non-negotiable, it is a track rule.
- Payment handshake uses x402 (HTTP 402, payment terms, retry with proof).
- Demo must be end to end.

## Merchant integration contract

Plug and play. A merchant platform that wants agent customers exposes exactly two endpoints.
We do not scrape, we do not drive browsers, we do not handle per-platform checkout quirks.

### 1. `GET /listings` (discovery)
Query params: `q`, `max_price`, `currency`.

```json
{
  "quote_id": "q_01HX...",
  "expires_at": "2026-08-15T14:32:00Z",
  "merchant": { "id": "mrc_x", "address": "0xAbc...", "name": "Example SG" },
  "items": [{
    "sku": "TB-SOFT-2PK",
    "title": "Soft bristle toothbrush, 2 pack",
    "description": "...",
    "price": { "amount": "4.20", "currency": "XSGD" },
    "availability": "in_stock"
  }],
  "basket_hash": "0x9f2c..."
}
```

The merchant MUST return `quote_id`, `expires_at`, and `basket_hash` from `/listings`, not
invent them at purchase time. The mandate binds to what the buyer actually saw.

### 2. `POST /purchase` (checkout)
Body: `sku`, `quantity`, `quote_id`, mandate credential, signed request.
Returns `402` with payment terms plus `basket_hash`, then settles on retry with proof.

For the demo we seed a **stub marketplace** implementing both endpoints. It is a demo harness,
not product. It is also where we plant the poisoned listing for the injection demo.

## Core primitive: the mandate

A signed credential scoped to one purchase intent:

```
{
  mandateId, principal, agentId,
  maxAmount, currency, expiresAt,
  intent,            // "toothbrush under $5"
  merchantAddress,   // null at mint, bound at verification
  basketHash,        // null at mint, bound at verification
  nonce
}
```

Signed by the issuer key in KMS at mint. The Verifier recomputes the hash and checks the
signature, so a tampered `maxAmount` is caught before anything moves.

**Important consequence of the flow below:** the mandate is minted BEFORE the product is
chosen, so `merchantAddress` and `basketHash` are empty at mint. The human approved a budget
and an intent, not a specific SKU. That gap is exactly what the Evaluator Agent exists to
close. Do not claim basket-level binding at approval time. Claim budget binding at approval
time plus intent verification before settlement.

## Runtime flow

1. **Buyer states intent.** "Toothbrush under $5." This is the delegation. Cap and intent
   come from the user's own words.
2. **Purchase Orchestrator calls Mandate Service.** Mandate minted, signed via KMS, stored.
   Cap and expiry set. Merchant and basket still empty.
3. **Orchestrator invokes the Browser Agent** (ours, internal). It calls `GET /listings` on
   onboarded merchant platforms and selects a candidate product. Selection logic is
   deliberately uninteresting; it is not where trust lives.
4. **Evaluator Agent** receives the candidate listing and the original intent. It checks:
   - Does the product match what the user asked for, or is it a substitution?
   - Is the price within the mandate cap?
   - Does the listing text contain prompt injection or instructions aimed at the agent?
   Outputs a **risk score 1 to 10** plus reasons. Structured output only, never free text
   passed downstream.
5. **Verifier Service** takes the mandate, the charge, and the evaluator output. Returns
   PASS, REVIEW, or FAIL with a reason code (see Verdict model). Deterministic checks are
   non-negotiable and run first (signature, expiry, revocation, cap, nonce). The risk score
   is one input among them, not the decision itself. Thresholds are config, not code.
6. **Emit to SQS.** Settlement Worker consumes. It mints a one-time card via the StraitsX
   card-issuing MCP with the budget set to the approved price, not the mandate cap.
7. **Payment executes on the x402 rail**, XSGD on Avalanche C-Chain. Tx hash and outcome
   written to DynamoDB and CloudWatch.

### Open decision, resolve before building step 6/7
Steps 6 and 7 name two different rails. Pick one primary path and make the other explicit:
- **Card primary:** mint the StraitsX one-time card, settle through it. x402 is then the
  handshake that carries payment terms, not the settlement rail. Simpler, but weaker Avalanche
  story.
- **x402 primary:** settle XSGD directly onchain via the contract. The StraitsX card becomes
  the fallback adapter for merchants who take cards but have not integrated.
Default to **x402 primary** unless the onchain leg is failing by end of day 1. It is what the
Avalanche prize rewards and what makes the revert demo possible.

## Services

Boundaries matter. Do not merge these.

### Composite (request/response orchestration)
- **Purchase Orchestrator.** Entry point for a purchase intent. Calls Mandate Service, invokes
  Browser Agent, passes output to Evaluator Agent, hands everything to Verifier. On PASS
  enqueues to SQS and returns 202. On REVIEW holds the charge and pushes it to the approval
  UI. On FAIL returns a reason code and stops.
- **Onboarding Orchestrator.** Registers merchant address, callback, and public key. Registers
  agent keypairs. Can be seeded by script for the demo.

### Atomic (single responsibility)
- **Mandate Service.** Mint, revoke, kill switch. The ONLY service that calls KMS to sign.
- **Verifier Service.** Pure decision function. Input: mandate, charge, evaluator output.
  Output: PASS, REVIEW, or FAIL with reason code. No network calls, no side effects. Keep it
  testable.
- **Merchant Registry.** Registered merchant addresses, callbacks, and listing endpoints.
  Answers "is this a legitimate counterparty" and backs `GET /merchants` for discovery.
- **Agent Registry.** See below.

### Agents (ours, internal)
- **Browser Agent.** Calls merchant `/listings`, selects a candidate. Plain tool-use loop.
  Assume it is compromisable. Nothing downstream trusts its output.
- **Evaluator Agent.** Intent match, price check, injection detection. Returns a structured
  risk score. Never sees the mandate signature or any key material.

Both are plain tool-use loops. No LangGraph, no CrewAI. Frameworks add dependency risk at 3am
for zero demo value.

### Async
- **Settlement Worker.** SQS consumer, not an orchestrator. Signs via KMS, executes on the
  chosen rail, writes tx hash and outcome to DynamoDB and CloudWatch. Lambda with SQS event
  source is fine.

## Where the Agent Registry sits now

In the original design the buyer's agent was the customer's, running outside our boundary, and
the registry existed to answer "is this external agent who it claims to be." In this flow the
Browser Agent is ours, so that question mostly disappears. The registry keeps two real jobs:

1. **Internal agent identity for the audit trail.** Every agent (Browser, Evaluator, and any
   future one) gets a keypair and signs its output. The audit log then records which agent
   produced which decision, and the Verifier can reject an evaluator result that is not signed
   by a registered evaluator. This blocks a compromised Browser Agent from forging its own
   clean risk score.
2. **The slot for third-party agents.** The moment we let a customer bring their own agent, the
   registry is what makes that safe. Keep the interface, keep it thin.

It is NOT on the critical path for the demo. If time is short, stub it to a static keypair map
and say so. Do not delete it from the architecture; the audit-signing job is what stops step 4
from being trivially bypassable.

## Verdict model

Three outcomes, not two. The split is by whether the failing check is deterministic or a
judgement call.

- **FAIL. No override, ever.** Bad signature, expired mandate, revoked mandate, over cap,
  replayed nonce, unregistered merchant, payout address mismatch. These are cryptographic or
  arithmetic facts. A user cannot click past them. If they want to spend more, that is a new
  mandate, not an approval button. Letting a human override these would train them to click
  through everything and would destroy the core claim.
- **REVIEW. Human decides.** Risk score in the middle band, suspected product substitution,
  price far below market, unknown or new sub-seller, injection suspected but not confirmed.
  The agent pauses. The user sees the listing, the price, and the Evaluator's reasons, then
  approves or kills it. Approval here re-signs the mandate with the merchant and basket now
  bound, then re-enters the Verifier. It does not skip verification.
- **PASS.** Everything clean. Straight to SQS.

Thresholds live in config. Default: score 1-3 PASS, 4-7 REVIEW, 8-10 FAIL. Tune during the
demo rehearsal, not during the demo.

Implementation note: REVIEW needs a hold state and a timeout. If nobody responds inside the
mandate window, it expires as a FAIL. Do not build an indefinite pending queue.

## Sub-seller impersonation

The Merchant Registry knows the **platform**, not the sellers on it. A marketplace onboards
once, then any scam seller inside it can list an iPhone for S$5. We verified the platform, not
the individual seller. Two mitigations:

1. **Payout address binding.** XSGD goes to the platform's registered address only, never to an
   address supplied in a listing payload. A scam seller inside the platform cannot redirect
   funds, and the platform stays accountable for its own sellers. Enforce this in the Verifier:
   `charge.payout_address` must equal the address on file in Merchant Registry, or FAIL. This
   is a deterministic check, not a judgement call. Costs nothing and closes the money path.
2. **Seller identity in the listing payload.** Require `seller_id`, account age, and rating
   count in each item. The Evaluator treats a brand new seller with a far-below-market price as
   high risk, which routes to REVIEW. Merchants already hold this data, so asking for it is
   cheap.

Position for Q&A: we push sub-seller reputation to the platform, because they already run
reputation systems and we should not rebuild one. What we guarantee is that money cannot leave
the registered payout address regardless of what a listing claims.

## Onchain

- **MandateRegistry** contract on Avalanche C-Chain. Stores mandate hash, agent address,
  merchant address, cap, expiry, revoked flag.
- Settlement calls `spend(mandateId, merchant, amount, basketHash)`. Contract validates and
  transfers XSGD, or reverts.
- The ledger is the source of truth for money. Nothing offchain can move funds or override a revert.
- Deploy and test on Fuji testnet FIRST. Only touch mainnet once the flow works.

## Offchain vs onchain split

Onchain: enforcement and record (cap, counterparty, expiry, revocation, transfer, events).
Offchain: transport and pre-flight (x402 handshake, listings lookup, quoting, evaluation,
injection inspection). These physically cannot run onchain.

## Adapters

Keep rail selection behind one interface so the mandate check is rail-agnostic.

- `x402 + XSGD onchain` LIVE
- `StraitsX card-issuing MCP` LIVE (sandbox then production)
- `AP2` interface slot, NOT IMPLEMENTED
- `Identity Adapter (Visa TAP / Mastercard KYA)` slot, NOT IMPLEMENTED. Belongs to Agent
  Registry, not Settlement Worker.

Do not write code that implies Visa or Google integration exists. Slots stay stubs.

## AWS

- ECS Fargate or App Runner for services
- API Gateway plus WAF at the edge. Rate limiting and payload hygiene only. **Do NOT enable
  Bot Control**, our callers are all bots by design.
- KMS for the issuer signing key AND the hot wallet key. No private key material anywhere else.
  Asymmetric key, `ECC_SECG_P256K1`, usage `SIGN_VERIFY`.
- No Secrets Manager. RPC URL is public config, or SSM Parameter Store SecureString.
- DynamoDB for mandates, registries, audit log. PITR on.
- SQS plus DLQ between verification and settlement.
- CloudWatch Logs plus a decision dashboard. This doubles as the demo screen.
- IAM role per service, least privilege.

### KMS signing gotcha
KMS returns DER-encoded signatures with no recovery id. For Avalanche you need `(r, s, v)`:
parse the DER, normalize to low-s per EIP-2, brute force `v` by recovering the address twice
and matching. Timebox this to 2 hours. If it stalls, fall back to a key in Parameter Store and
note the tradeoff.

## Threat model

What we stop:
- **Prompt injection in a listing.** Evaluator flags it, Verifier fails the charge. Even if the
  Evaluator is fooled, the mandate cap and expiry still hold and the contract reverts on
  violation. THIS IS THE DEMO.
- **Product substitution.** Evaluator compares candidate against the stated intent. This is the
  check that covers the gap left by minting the mandate before product selection.
- **Malicious merchant overcharging.** Price checked against cap, basket hash bound at
  verification.
- **Tampered mandate.** Signature check. A modified `maxAmount` fails before anything moves.
- **Replay.** Nonce and one-time mandate consumption.
- **Forged evaluator verdict.** Evaluator signs its output with a registered key.
- **Scam sub-seller inside a registered platform.** Payout address binding means funds can only
  reach the platform's registered address. Seller metadata routes suspicious listings to REVIEW.

What we do NOT stop, and say so plainly:
- A purchase that is within cap, matches intent, and passes evaluation, but is still not what
  the user would have picked. Judgement inside an approved mandate is not enforceable.
- The Evaluator is an LLM reading attacker-controlled text. It can be fooled. That is why it
  produces evidence for the Verifier rather than making the decision, and why the deterministic
  checks run regardless of the score.

Never claim we make the agent trustworthy. We bound what a compromised agent can spend and we
check its work before money moves.

## Untrusted input handling

Merchant listing payloads are attacker-controlled. Always:
- Validate against a strict schema, reject unexpected fields
- Cap field lengths, a 4000 word description is itself a signal
- Never interpolate listing text into a system prompt, wrap it explicitly as untrusted data
- Log anything suspicious so it surfaces on the dashboard

## Demo flow

1. User states intent, "toothbrush under $5"
2. Mandate minted and shown in the approval UI, cap and window visible
3. Browser Agent pulls listings from the stub marketplace, picks a product
4. Evaluator scores it, dashboard shows the score and reasons
5. Verifier PASSes, card minted or x402 settles, XSGD moves on C-Chain
6. Receipt plus audit trail, tx hash links to Snowtrace
7. **Re-run against the poisoned listing.** Description contains an injection telling the agent
   to buy a gift card or overspend. Evaluator flags it, Verifier FAILs, and if the agent is
   forced past that, the contract reverts on mainnet. Show both the rejection log and the
   reverted tx.
7b. **Show a REVIEW.** Same listing, milder tampering: right product, price suspiciously low,
   unknown seller. Agent pauses, approval UI surfaces the Evaluator's reasons, human kills it.
   This is where the human-in-the-loop story lands.
8. Optional coda: B2B procurement on a standing mandate, no human in the per-purchase loop.

Step 7 is what makes judges remember us. Protect it.

## Frontend

One small React app hitting the API. No auth, no design system, no component library beyond
Tailwind. Build after the mandate check and settlement work.

- **Mandate approval UI.** Human sees the scope in plain language ("up to S$5, next 10
  minutes") and approves. Makes the mandate visible instead of JSON.
- **Decision dashboard.** Live feed of PASS / REVIEW / FAIL with reason codes, risk scores,
  mandate id, and tx hash linking to Snowtrace. Must STREAM (SSE or websocket), not poll on a
  button press. A row flipping to FAIL in real time lands far harder than a refresh.
- **REVIEW approval surface.** When a charge holds, show the listing, the price, and the
  Evaluator's reasons. Approve or kill. Can be part of the dashboard rather than its own page.
- **Buyer agent chat UI.** The intent surface. Where the user types "toothbrush under $5".
- **Stub marketplace page.** Rendered listing view for the demo merchant. Lowest priority.

## Build order

1. Mandate Service plus Verifier (core, do first)
2. Stub marketplace with `GET /listings` and `POST /purchase`
3. MandateRegistry contract on Fuji testnet
4. Settlement leg with real XSGD (riskiest for time, sort wallet and gas early)
5. Browser Agent plus Evaluator Agent
6. Agent Registry (stub is acceptable)
7. Frontend

Cuts if time runs out, in order: stub marketplace UI, B2B coda, Agent Registry beyond a static
map, WAF, Settlement Worker as its own service (fold into Verifier and go synchronous).

## Workstream split

Four independent tracks, roughly equal. Written for a team of 4. Collapse to 3 by folding D
into A and B. Expand to 5 by splitting C into contract work and settlement work.

The tracks are decoupled by **contract-first setup**: on hour one, agree the JSON shapes for
mandate, charge, evaluator output, and verdict, and commit them as fixtures. After that
everyone codes against fixtures, not against each other's running services. Nobody blocks.

### A. Mandate and Verifier (the core)
- Mandate Service: mint, revoke, kill switch, KMS signing
- Verifier Service: signature, expiry, revocation, cap, nonce, payout address binding
- Verdict model, thresholds in config, reason codes
- DynamoDB schema for mandates and audit log
- Unit tests. This is the one piece that must be defensible under questioning.
- Owns: the fixture definitions everyone else codes against

### B. Agents and merchant surface
- Stub marketplace: `GET /listings`, `POST /purchase`, quote_id, basket_hash, seller metadata
- Poisoned listing variants for the demo (injection, substitution, low-price/new-seller)
- Browser Agent: plain tool-use loop, calls listings, picks a candidate
- Evaluator Agent: intent match, price check, injection detection, structured risk score
- Untrusted input handling: schema validation, field length caps, no prompt interpolation
- Depends only on the fixture shapes, not on A being finished

### C. Chain and settlement
- MandateRegistry contract, Fuji testnet first, then mainnet
- Wallet funding: XSGD plus AVAX gas. **Do this in hour one, it is the longest lead time.**
- x402 handshake: 402 response, payment terms, retry with proof
- Settlement Worker: SQS consumer, KMS signing, `spend()` call, tx hash writeback
- StraitsX card MCP adapter as the fallback rail
- Highest risk track. If this slips, the demo has no revert moment.

### D. Platform, frontend, and pitch
- AWS: Fargate/App Runner, API Gateway, WAF, SQS, DynamoDB, KMS, IAM roles
- CI or a deploy script, whatever gets services up repeatably
- Purchase Orchestrator and Onboarding Orchestrator wiring once A/B/C expose endpoints
- Frontend: mandate approval UI, streaming decision dashboard, REVIEW approval surface, chat UI
- Well-Architected slide mapping choices to the five pillars
- Pitch deck and the 1 minute elevator script

### Integration points
- End of day 1: A and B integrate against real services, C has testnet settling
- Mid day 2: full path end to end on testnet, D has it deployed
- Day 2 evening: mainnet cutover, rehearse the demo three times including the failure cases
- Do not leave integration to the last block. Merge to main often.

## Positioning notes

- We are infrastructure, not a marketplace. We never host merchant catalogues.
- Two endpoints is the same shape ACP and UCP landed on. Say that rather than defending it as a
  shortcut.
- Full enforcement needs merchant integration. Degraded enforcement (cap, merchant lock, expiry,
  no basket binding) works via the card rail. Coverage gradient, not a wall.
- Browser-driven checkout on unintegrated sites is deliberately out of scope. It depends on
  evading bot protection the merchant chose to deploy. We chose not to, we are not unable to.
- Closed-loop versions of this exist at scale (Alipay AI Pay, ACP, UCP). The neutral cross-rail
  version does not. That is the honest claim. Do not say "nobody has built this".
- The Evaluator is a heuristic layer on top of deterministic enforcement, never a replacement
  for it. If asked, be clear which checks are which.

## Pitch line

We do not trust the agent, we trust the rail.