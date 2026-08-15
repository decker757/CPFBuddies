# Contracts

The JSON shapes workstreams **B**, **C** and **D** build against. Owned by
workstream A, regenerated with:

```bash
python -m trustrail.contracts.export
```

Code against these files, not against a running service. Nobody blocks on
anybody.

- `schemas/` — JSON Schema for each published model.
- `fixtures/` — complete `VerificationRequest` payloads and the verdict each one
  must produce. See [FIXTURES.md](FIXTURES.md) for the table.

The fixtures are also the Verifier's test corpus, so they cannot drift from
behaviour: `tests/test_contract_fixtures.py` fails if the committed JSON stops
matching what the code generates.

## What each workstream needs

### B — agents and merchant surface

- `EvaluatorOutput` / `SignedEvaluatorOutput` is your output contract.
- `risk_score` is 1–10. Thresholds are ours, not yours: emit the score and the
  flags, and let the Verifier decide. Default bands are 1–3 PASS, 4–7 REVIEW,
  8–10 FAIL.
- **Sign your output.** The Verifier rejects an evaluation that is not signed by
  a key registered to that evaluator in the Agent Registry. Digest is
  `keccak256` over canonical JSON — use `trustrail.signing.evidence.evaluation_digest`
  so we cannot disagree about whitespace.
- **Fill in `subject`.** It binds the evaluation to one mandate, basket and
  amount. Without it, a clean score from a S$4 toothbrush could be replayed onto
  a S$4000 gift card, and the signature would buy nothing.
- `Charge.payout_address` must be the platform's registered address. An address
  taken from a listing payload will FAIL.
- Listing text is attacker-controlled. `reasons` are capped at 10 entries of 280
  characters, and every model rejects unexpected fields.

### C — chain and settlement

- **`mandate_digest` is the cross-workstream contract.** The MandateRegistry
  contract must recompute the identical value in Solidity, or `spend()` will not
  match what we signed. The type string is in
  `src/trustrail/signing/eip712.py`; `tests/test_eip712.py` pins a golden digest
  for the canonical demo mandate. If that test fails, tell workstream A — do not
  update the constant.
- Amounts cross the boundary as **integer minor units** (`Money.minor_units`),
  never as decimal strings. XSGD is treated as 6 decimals — **please confirm
  against the deployed token before mainnet.**
- Unbound `merchant_address` and `basket_hash` encode as `address(0)` and
  `bytes32(0)`.
- The EIP-712 domain (`config/verifier.toml`) must match what the contract is
  deployed with: chain id 43113 on Fuji, 43114 on mainnet, and
  `verifying_contract` set to the registry address once it exists.
- Only settle a charge whose verdict is `PASS`, and call
  `POST /mandates/{id}/consume` first. It succeeds exactly once, so it is what
  stops two workers double-spending one mandate.

### D — platform and frontend

- `Verdict` is the dashboard payload. `checks` is the full trace; each entry is
  tagged `DETERMINISTIC` or `JUDGEMENT`.
- **`failed_deterministically` decides whether to render an override button.**
  If it is true, there must be no button — a bad signature or an over-cap charge
  is a fact, and letting a human click past it would train them to click past
  everything. A REVIEW is where the human belongs.
- `failed_deterministically` on `Verdict`, and `approvable` on `ReviewHold`, are
  **computed: emitted but never accepted.** Send either one back and it is
  dropped and recomputed, because a caller asserting its own answer to "was this
  a fact or a threshold" is the one claim this system does not take on trust.
  Do not re-derive them from `checks` client-side either — that puts a
  safety rule in two languages and lets them drift.
  (Both were plain Python properties until 2026-08-16, which meant neither
  appeared in the JSON at all. A dashboard read `undefined`, took it as falsy,
  and would have offered a button that clicks past a bad signature. If you are
  reading a schema generated before that date, this field is missing from it.)
- `reason_codes` are stable strings, safe to key translations off.
- `AuditEntry` is the streaming feed. `VERDICT_ISSUED` entries carry the whole
  verdict.
- The flow arrives as four beats for one purchase, in this order: `MANDATE_MINTED`,
  `CANDIDATE_SELECTED`, `EVALUATION_COMPLETE`, `VERDICT_ISSUED`. Settlement adds a
  `SETTLEMENT_*` entry once the worker has run.
- **`CANDIDATE_SELECTED` and `EVALUATION_COMPLETE` share a timestamp**, because
  workstream B returns the candidate and the evaluation in one round trip and the
  orchestrator never sees when either agent finished. Order the feed by arrival,
  not by `occurred_at`, and stagger the reveal in the UI if you want them to land
  separately — the log records what was known, not a sequence nobody observed.
- `EVALUATION_COMPLETE` carries the full `EvaluatorOutput` on `evaluation`: risk
  score, flags, and the Evaluator's own reasons. Those reasons are **not** on the
  `Verdict`, and a hold is the only other place they appear, so this is where a
  PASS or a FAIL gets its "why".
- Every `summary` is clipped to 500 characters and carries merchant- and
  LLM-supplied text. Render it as text, never as markup.

#### Reading the feed

- `GET /audit?since=&limit=` is the snapshot, for the dashboard's first paint.
- `GET /audit/stream` is the live feed: Server-Sent Events, one frame per entry,
  `event:` set to the event type and `data:` to the whole `AuditEntry` as JSON.
- **`id:` is a position in the feed, not an `event_id`.** It points *past* the
  entry it arrives with, so reconnecting from it asks for what comes next. A
  browser `EventSource` resends it as `Last-Event-ID` by itself and resumes with
  no rows lost; pass `?since=` to override that after painting from `GET /audit`.
- **Do not combine `?since=` with a plain `EventSource`.** The URL is fixed for
  the connection's lifetime and `since` wins over `Last-Event-ID`, so every
  automatic reconnect would replay from the same point forever. Either pass
  `since` and reconnect yourself, or omit it and de-duplicate on `id:`.
- Order the feed by arrival. Do not re-sort by `occurred_at` — entries can share
  a timestamp, and re-sorting is what makes a cursor skip or repeat rows.
- The Verifier takes everything in its request body and looks nothing up. The
  Purchase Orchestrator assembles that payload: mandate state, merchant record,
  evaluator record, kill-switch state and `now`.

## Two deliberate deviations from CLAUDE.md

1. **`max_amount` bundles amount and currency** (`{"currency": "XSGD", "amount":
   "5.00"}`) instead of being two sibling fields. This is the shape the merchant
   contract already uses for prices, and it makes an amount/currency mismatch
   unrepresentable rather than something to remember to check.
2. **Reason codes live in the wire models**, not inside the Verifier, because
   the dashboard and the audit log render them and both are downstream of A.
