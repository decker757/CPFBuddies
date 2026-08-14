# CLAUDE.md

Context for AI coding agents working on this repo. Read this before writing code.

## Working in this repo
Read this before exploring codebase.

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- Service boundaries in this repo are load-bearing (see Services below).
  Before adding a call between services, run `graphify path "<A>" "<B>"` to
  check you are not creating a cycle or bypassing the Verifier.

## What we are building

A trust rail for agent payments. The layer that lets an AI agent spend real money on
someone's behalf without the human having to trust the agent.

Core claim: **we do not trust the agent, we trust the rail.** Enforcement happens at
settlement, outside the agent, and is publicly verifiable onchain.

Hackathon track: Agentic Payments Infrastructure.
Also targeting: Avalanche "Best Use of x402", AWS "Best Architected", StraitsX "Real-World Impact".

## Hard requirements

- All settlement MUST use $XSGD on Avalanche C-Chain Mainnet. Non-negotiable, it is a track rule.
- Payment handshake uses x402 (HTTP 402 + payment terms + retry with proof).
- Demo must be end to end.

## Core primitive: the mandate

A signed credential scoped to one purchase:

```
{
  mandateId, principal, agentId, merchantAddress,
  maxAmount, currency, expiresAt, basketHash, nonce
}
```

Signed by the issuer key in KMS. The Verifier checks it. The onchain MandateRegistry
enforces it. A charge that violates the mandate reverts.

## Services

Boundaries matter. Do not merge these.

### Composite (request/response orchestration)
- **Purchase Orchestrator** — entry point the seller calls. Fans out to Agent Registry,
  Merchant Registry, Mandate Service, Verifier. On PASS enqueues to SQS and returns 202.
  On REJECT returns a reason code.
- **Onboarding Orchestrator** — registers buyer agent keypair, registers merchant address
  and callback, mints first mandate. Can be seeded by script for the demo.

### Atomic (single responsibility)
- **Mandate Service** — mint, revoke, kill switch. The ONLY service that calls KMS to sign.
- **Verifier Service** — pure decision function. Input: mandate + charge. Output: PASS or
  REJECT with reason code. No network calls, no side effects. Keep it testable.
- **Agent Registry** — agentId to public key. Answers "is this agent who it claims to be".
- **Merchant Registry** — registered merchant addresses and callbacks. Answers "is this a
  legitimate counterparty".

### Async
- **Settlement Worker** — SQS consumer, not an orchestrator. Signs via KMS, calls `spend()`
  on MandateRegistry, writes tx hash and outcome to DynamoDB and CloudWatch. Lambda with
  SQS event source is fine.

## Onchain

- **MandateRegistry** contract on Avalanche C-Chain. Stores mandate hash, agent address,
  merchant address, cap, expiry, revoked flag.
- Settlement calls `spend(mandateId, merchant, amount, basketHash)`. Contract validates and
  transfers XSGD, or reverts.
- The ledger is the source of truth for money. Nothing offchain can move funds or override
  a revert.
- Deploy and test on Fuji testnet FIRST. Only touch mainnet once the flow works.

## Offchain vs onchain split

Onchain: enforcement and record (cap, counterparty, expiry, revocation, transfer, events).
Offchain: transport and pre-flight (x402 handshake, catalogue lookup, quoting, identity
check, injection inspection). These physically cannot run onchain.

## Adapters

Keep rail selection behind one interface so the mandate check is rail-agnostic.

- `x402 + XSGD onchain` — LIVE
- `StraitsX card-issuing MCP` — LIVE (sandbox then production)
- `AP2` — interface slot, NOT IMPLEMENTED
- `Identity Adapter (Visa TAP / Mastercard KYA)` — slot, NOT IMPLEMENTED. Belongs to Agent
  Registry, not Settlement Worker.

Do not write code that implies Visa or Google integration exists. Slots stay stubs.

## AWS

- ECS Fargate or App Runner for services
- API Gateway + WAF at the edge. Rate limiting and payload hygiene only. **Do NOT enable
  Bot Control** — our callers are all bots by design.
- KMS for the issuer signing key AND the hot wallet key. No private key material anywhere
  else. Asymmetric key, `ECC_SECG_P256K1`, usage `SIGN_VERIFY`.
- No Secrets Manager. RPC URL is public config, or SSM Parameter Store SecureString.
- DynamoDB for mandates, registries, audit log. PITR on.
- SQS + DLQ between verification and settlement.
- CloudWatch Logs plus a decision dashboard. This doubles as the demo screen.
- IAM role per service, least privilege.

### KMS signing gotcha
KMS returns DER-encoded signatures with no recovery id. For Avalanche you need `(r, s, v)`:
parse the DER, normalize to low-s per EIP-2, brute force `v` by recovering the address twice
and matching. Timebox this to 2 hours. If it stalls, fall back to a key in Parameter Store
and note the tradeoff.

## Agent implementation

No LangGraph, CrewAI, or orchestration frameworks. The buyer agent is a plain tool-use loop
with a few tools (search catalogue, request mandate, pay). Easier to debug at 3am and adds
zero demo value otherwise. Model choice is the customer's, so keep the interface to signed
HTTP request plus mandate credential.

## Threat model

What we stop:
- Fake agent impersonating a real one → signature check against Agent Registry
- Hijacked agent (prompt injection) → mandate violation, contract reverts. THIS IS THE DEMO.
- Stolen agent key → damage capped at one merchant, one amount, one window. Plus revocation.
- Malicious seller overcharging → basket hash binds mandate to the exact quote
- Replay → nonce / one-time mandate consumption

What we do NOT stop, and say so plainly:
- A compromised agent making a legitimate-looking purchase inside its mandate. If the human
  authorised S$38 at that merchant and the agent buys the wrong item there, it settles.

Never claim we make the agent trustworthy. We bound what a compromised agent can spend.

## Demo flow

1. Seller onboards: catalogue + x402 endpoint registered
2. Buyer agent queries catalogue, gets options and total
3. Human approves a scoped mandate, visibly (amount, merchant, time window)
4. Buyer agent hits seller's x402 endpoint, presents mandate and signed request
5. Rail verifies before settlement, XSGD moves on C-Chain
6. Signed receipt plus audit trail
7. **Re-run with a poisoned payload.** Hijacked agent tries to overspend or redirect.
   Transaction REVERTS on mainnet, visible on Snowtrace. Show the rejection log.
8. Optional coda: B2B procurement agent restocks on a standing mandate. Same rail, different client.

Step 7 is what makes judges remember us. Protect it.

## Frontend

One small React app hitting the API. No auth, no design system, no component library beyond
Tailwind. Budget half a day, and only start it after the mandate check and settlement work.

### Required
- **Mandate approval UI** — human sees the scope in plain language ("S$38, this merchant,
  next 10 minutes") and approves. This is what makes the mandate visible instead of JSON.
  It is the demo's money shot.
- **Decision dashboard** — live feed of PASS / REJECT with reason codes, mandate id, and tx
  hash linking to Snowtrace. This is the screen we point at when the injection attack reverts.
  Must STREAM (SSE or websocket), not poll on a button press. A row flipping to REJECT in real
  time lands far harder than a refresh.

### Optional
- Chat UI for the buyer agent, only if we do the B2C flow. A terminal is acceptable.
- Mock seller catalogue page. Raw JSON is fine, nobody needs it rendered.

## Build order

1. Mandate Service + Verifier (core, do first)
2. MandateRegistry contract on testnet
3. Settlement leg with real XSGD (riskiest for time, sort wallet and gas early)
4. Seller kit + mock seller
5. Identity hook (stub, do not overbuild)
6. Demo harness and dashboard

Cuts if time runs out, in order: second buyer flow, identity stub, WAF, Settlement Worker as
its own service (fold into Verifier and go synchronous).

## Positioning notes

- We are infrastructure, not a marketplace. We never host seller catalogues. Hosting their
  inventory would make us a competitor to our own customers.
- M2M in the StraitsX sense means the payee is a machine selling machine services (agent pays
  an API, sub-cent, per call). Autonomous B2B procurement is "Business buying", not M2M.
  Do not overclaim this in copy or the pitch.
- The primitives exist (x402, AP2, TAP, KYA, one-time cards). The neutral cross-rail
  enforcement layer does not. That is the honest claim.

## Pitch line

We do not trust the agent, we trust the rail.


