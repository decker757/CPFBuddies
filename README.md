# CPFBuddies — TrustRail

A trust rail for agent payments: the layer that lets an AI agent spend real
money on someone's behalf without the human having to trust the agent.

**We do not trust the agent, we trust the rail.** Enforcement happens outside the
agent, at settlement, and is publicly verifiable onchain.

This repository currently contains **workstream A**: the Mandate Service and the
Verifier Service, plus the contract fixtures the other workstreams build
against. See [CLAUDE.md](CLAUDE.md) for the full architecture.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,aws]"
.venv/bin/python -m pytest          # full suite, offline, no AWS account
```

Run the rail locally — a generated issuer key, everything in memory:

```bash
.venv/bin/uvicorn --factory trustrail.app:create_demo_app
```

## What is here

| Path | What it is |
| --- | --- |
| `src/trustrail/models/` | The wire contract. Strict Pydantic models, unexpected fields refused. |
| `src/trustrail/mandate/` | Mint, bind, revoke, consume, kill switch. The only thing that signs. |
| `src/trustrail/verifier/` | The decision function. PASS, REVIEW or FAIL with reason codes. |
| `src/trustrail/signing/` | EIP-712 digests, secp256k1, the KMS adapter. |
| `src/trustrail/stores/` | In-memory and DynamoDB implementations of the same ports. |
| `src/trustrail/ports.py` | The five seams between this package and the world. |
| `contracts/` | Generated schemas and golden fixtures — see [contracts/README.md](contracts/README.md). |
| `config/verifier.toml` | Thresholds and the EIP-712 domain. |

## The three ideas worth knowing

**The Verifier is a pure function.** It makes no network calls, has no side
effects, and does not read a clock — mandate state, merchant record, evaluator
record and `now` all arrive in the request. That is what makes the one component
that must be defensible under questioning also the one that is exhaustively
testable.

**Facts and judgements are kept apart.** Deterministic checks — signature,
expiry, revocation, cap, nonce, payout address — run first, short-circuit, and
always FAIL. They cannot be overridden, because a human clicking past a bad
signature is not consent. Judgement checks (risk score, injection, substitution)
run only once every fact has passed, and route to REVIEW where a person belongs.
Every entry in the verdict trace says which kind it is.

**The mandate is minted before the product is chosen.** The buyer approved a
budget and an intent, not a SKU, so `merchant_address` and `basket_hash` are
empty at mint. We do not claim basket-level binding at approval time. We claim
budget binding at approval, and intent verification before settlement.

## Regenerating the contracts

```bash
.venv/bin/python -m trustrail.contracts.export
```

Output is deterministic. A clean `git diff` means the wire contract did not
move; a dirty one is the signal to tell workstreams B, C and D.

## Deploying

```python
from trustrail.stores.schema import create_tables
create_tables()          # four tables, PITR on, TTL on review holds
```

Point `TRUSTRAIL_ISSUER_ADDRESS` at the address of the KMS key and swap
`LocalSigner` for `KmsSigner`. The key never leaves KMS; the Verifier only ever
needs the address.

## Known gaps

- XSGD is treated as 6 decimals. Confirm against the deployed token on Avalanche
  C-Chain before mainnet cutover.
- `verifying_contract` in the EIP-712 domain is the zero address until
  workstream C deploys the MandateRegistry.
- Review holds have a model, a store and deadline arithmetic here; creating and
  resolving them is the Purchase Orchestrator's job (workstream D).
