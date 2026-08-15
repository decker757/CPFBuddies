# Frontend

The intent surface and the decision dashboard. React, Vite, Tailwind, nothing else.

```bash
npm install
npm run dev            # http://localhost:5173
```

It expects the rail on `http://localhost:8000`. Override with `VITE_API_BASE`.

```bash
# terminal 1 — offline, nothing settles
.venv/bin/uvicorn --factory app.rail:demo_app

# terminal 1 — wired to a chain, XSGD actually moves
TRUSTRAIL_NETWORK=avalanche TRUSTRAIL_RPC_URL=... \
TRUSTRAIL_REGISTRAR_KEY=0x... TRUSTRAIL_SETTLER_KEY=0x... \
.venv/bin/uvicorn --factory app.rail:chain_app
```

Port 5173 is not arbitrary — it is what the API allows by default. Move it and set
`TRUSTRAIL_CORS_ORIGINS` to match.

## Walking the demo

The Browser Agent picks by lowest price, so **an honest intent cannot reach the PASS
beat**: the good S$4.20 toothbrush is undercut by the injected listing at S$4.00 (which
has an identical title) and the unrated seller's at S$0.50. Pin the listing instead, and
restart the API between beats:

| `TRUSTRAIL_DEMO_SKU` | Outcome | What it shows |
| --- | --- | --- |
| unset | REVIEW | Honest selection. The cheapest listing wins and it is the suspicious one. |
| `TB-SOFT-2PK` | PASS | The clean purchase. Settles, and links to Snowtrace when on-chain. |
| `TB-SUSPICIOUS` | REVIEW | The modal: unrated seller, price far below market. Approve or kill. |
| `TB-INJECTION` | FAIL | Prompt injection in the description. Nothing is queued. |
| `GIFT-SUBSTITUTE` | FAIL | Substitution — but only against a toothbrush intent. |

Two intents reach a beat with no pinning at all: `toothbrush under $5` lands in REVIEW,
and `gift card` lands on the injected listing and FAILs.

## What is here

| Path | What it is |
| --- | --- |
| `src/types.ts` | The wire contract in TypeScript. Mirrors `contracts/schemas/`. |
| `src/api.ts` | Fetch wrappers. `ApiError` carries the status, because 409 is a real answer. |
| `src/useAuditStream.ts` | The SSE feed. De-duplicates on the cursor id. |
| `src/steps.ts` | Audit events folded into the five beats a person follows. |
| `src/components/` | Intent bar, timeline, review modal, outcome card, badges. |
| `../DESIGN-replicate.md` | Where the tokens come from. Values only — see `src/index.css`. |

## Two rules this app keeps

**`verdict.failed_deterministically` and `hold.approvable` come from the server and are
never re-derived here.** They decide whether an override button may exist at all. Putting
that rule in TypeScript as well would let the two copies drift, and the direction it would
drift is towards offering a button that clicks past a bad signature.

**No `dangerouslySetInnerHTML`, anywhere.** Listing titles, audit summaries and Evaluator
reasons are all attacker-controlled — the injection demo is literally a merchant writing
instructions into a description. Everything renders as text.

## Known gaps

- Light theme only. The source design document has no dark palette, and a demo screen is
  a projector rather than a midnight editor.
- One purchase at a time. The timeline shows everything after the moment you pressed
  Delegate, which is the right answer for one run and the wrong one for two at once.
- No auth, matching the API. Both are fine on a laptop and neither is fine on a public URL.
