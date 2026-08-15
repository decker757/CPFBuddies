# CPFBuddies — TrustRail

A trust rail for agent payments: the layer that lets an AI agent spend real
money on someone's behalf without the human having to trust the agent.

**We do not trust the agent, we trust the rail.** Enforcement happens outside the
agent, at settlement, and is publicly verifiable onchain.

This repository contains **workstream A** — the Mandate Service and the Verifier
Service, plus the contract fixtures the other workstreams build against — and
**workstream C**, the chain and settlement leg. See [CLAUDE.md](CLAUDE.md) for
the full architecture.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,aws]"     # .venv/Scripts/ on Windows
.venv/bin/python -m pytest                # full suite, offline, no AWS account
```

The suite runs with no chain and no AWS. The settlement integration tests skip
themselves unless a local node is up:

```bash
cd onchain && npm install && npx hardhat test    # Solidity, 21 tests
cd onchain && npx hardhat node                   # terminal 1
cd onchain && npm run deploy:local               # terminal 2
.venv/bin/python -m pytest -m integration        # now they run
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
| `src/trustrail/settlement/` | The worker, the rails, the chain client, the queue. |
| `src/trustrail/x402/` | The 402 handshake wire format and client. Shared with workstream B. |
| `contracts/` | Generated schemas and golden fixtures — see [contracts/README.md](contracts/README.md). |
| `onchain/` | The Hardhat project: `MandateRegistry.sol`, `MockXSGD.sol`, deploy scripts. |
| `config/verifier.toml` | Thresholds and the EIP-712 domain. |

Two different things are called "contracts" here, so the directories are named
apart: `contracts/` is the **wire** contract (JSON Schema and fixtures, shared
across workstreams) and `onchain/` is the **smart** contract (Solidity).

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

**Settlement never re-decides, and the contract never trusts it.** The worker
takes a PASS and executes it; `MandateRegistry.spend` then re-checks the cap,
the merchant, the expiry and one-time consumption on its own. Both halves are
tested: `tests/test_settlement_integration.py` registers a mandate onchain with
*stricter* terms than the offchain record, watches the Verifier pass it, and
watches the contract refuse it anyway.

## Settlement outcomes

Three, and conflating the last two is the expensive mistake:

| Outcome | Meaning | Queue |
| --- | --- | --- |
| `SETTLED` | Money moved. | ack |
| `REFUSED` | The rail worked and declined — an onchain revert. | ack; retrying cannot change the answer |
| `ERROR` | The rail itself broke — RPC fault, nonce clash. | nack; redelivery may succeed |

A reverted transaction retried forever burns gas and never settles.

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

- **`verifying_contract` in the EIP-712 domain is still the zero address.** The
  MandateRegistry now exists, but pointing the domain at a deployed address
  changes every mandate digest, so the committed fixtures would have to be
  regenerated in the same commit. Do it as one coordinated change at Fuji
  cutover, not before.
- **The revert demo needs Fuji, not a local node.** Hardhat simulates and
  refuses to broadcast a doomed transaction, so there is no hash to open on an
  explorer. Public RPC mines it and it reverts with a receipt. Rehearse
  CLAUDE.md demo step 7 against Fuji.
- **XSGD is not deployed on Fuji**, so testnet uses `MockXSGD`. The token
  address is a deploy parameter; mainnet cutover sets `XSGD_ADDRESS`. The
  6-decimal assumption is now checked at load time against the deployed token —
  `Deployment.assert_decimals_match` raises rather than settling a wrong number.
- **The StraitsX card rail is a local fake.** No credentials yet; the protocol
  is the deliverable and the MCP call is a TODO in one method.
- **`SqsQueue`, `DynamoAuditLog` and `KmsSigner` are written but unwired.**
  Workstream D owns provisioning.
- Review holds have a model, a store and deadline arithmetic here; creating and
  resolving them is the Purchase Orchestrator's job (workstream D).
