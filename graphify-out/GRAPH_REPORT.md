# Graph Report - CPFBuddies  (2026-08-15)

## Corpus Check
- 3 files · ~1,506 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 26 nodes · 24 edges · 5 communities (3 shown, 2 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `5f407da5`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- CLAUDE.md
- Services
- Frontend
- AWS
- README.md

## God Nodes (most connected - your core abstractions)
1. `Services` - 4 edges
2. `Frontend` - 3 edges
3. `AWS` - 2 edges
4. `Working in this repo` - 1 edges
5. `What we are building` - 1 edges
6. `Hard requirements` - 1 edges
7. `Core primitive: the mandate` - 1 edges
8. `Composite (request/response orchestration)` - 1 edges
9. `Atomic (single responsibility)` - 1 edges
10. `Async` - 1 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Communities (5 total, 2 thin omitted)

### Community 0 - "CLAUDE.md"
Cohesion: 0.13
Nodes (13): Adapters, Agent implementation, Build order, Core primitive: the mandate, Demo flow, Hard requirements, Offchain vs onchain split, Onchain (+5 more)

### Community 1 - "Services"
Cohesion: 0.50
Nodes (4): Async, Atomic (single responsibility), Composite (request/response orchestration), Services

### Community 2 - "Frontend"
Cohesion: 0.67
Nodes (3): Frontend, Optional, Required

## Knowledge Gaps
- **20 isolated node(s):** `Working in this repo`, `What we are building`, `Hard requirements`, `Core primitive: the mandate`, `Composite (request/response orchestration)` (+15 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Services` connect `Services` to `CLAUDE.md`?**
  _High betweenness centrality (0.210) - this node is a cross-community bridge._
- **Why does `Frontend` connect `Frontend` to `CLAUDE.md`?**
  _High betweenness centrality (0.143) - this node is a cross-community bridge._
- **Why does `AWS` connect `AWS` to `CLAUDE.md`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **What connects `Working in this repo`, `What we are building`, `Hard requirements` to the rest of the system?**
  _20 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `CLAUDE.md` be split into smaller, more focused modules?**
  _Cohesion score 0.13333333333333333 - nodes in this community are weakly interconnected._