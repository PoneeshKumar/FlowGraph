# FlowGraph

Real-time money flow intelligence engine. Every payment is a directed edge in a live graph. The system detects fraud patterns — circular flows, hub-and-spoke networks, layering — that are invisible to row-based systems.

## Architecture

Five layers, top to bottom:

```
Payment events → Kafka → Python consumer (Faust)
                              ↓            ↓            ↓
                          Postgres      Neo4j        Redis
                         (outbox)      (graph)      (ZSET)
                                          ↓
                              Cycle detection (DFS)
                              Incremental PageRank
                              Louvain clustering (daily batch)
                                          ↓
                              Risk flag aggregator
                                          ↓
                              Claude API (subgraph summary → risk classification)
                                          ↓
                              FastAPI (Cypher-over-HTTP)
                              D3 / Cytoscape.js (force-directed graph UI)
```

## Current status

**Done and verified:**
- Kafka ingestion
- Python consumer (Faust)
- Cycle detection (DFS over `FLOWS_TO`) — benchmarked on IBM AML
- Louvain community detection (daily batch, networkx/Leiden) — benchmarked on IBM AML

**In progress:**
- Storage layer (Postgres + Neo4j + Redis dual-write)

**Not started:**
- Incremental PageRank
- AI enrichment layer
- Query API
- Frontend

## Stack

| Component | Technology |
|---|---|
| Message queue | Kafka |
| Stream processing | Python / Faust |
| Graph database | Neo4j |
| Relational store | Postgres |
| Cache / time-series | Redis |
| API | FastAPI |
| Frontend | D3 or Cytoscape.js |
| AI enrichment | Claude API (claude-sonnet-4-6) |

## Data model

**Neo4j nodes** — types: `account`, `merchant`, `bank`, `exchange`. Properties: `kyc_tier`, `risk_score`, `country`, `account_age`, `cumulative_volume`. The Louvain batch also writes `community_id` (12-hex-char) and `community_detected_at` (epoch seconds) onto `Account` nodes.

**Neo4j edges** — two directed edge types are maintained per payment:
- `TRANSFER` — one edge per transaction, keyed by `txn_id` (MERGE key). Properties: `amount_cents`, `ts` (unix seconds), `rail`, `event_type`. Full per-transaction detail; used for audit and per-txn tracing.
- `FLOWS_TO` — one aggregate edge per directed account pair. Properties: `tx_count`, `total_amount`, `first_ts`, `last_ts`, `min_amount`, `max_amount`, `rail`. Graph algorithms (cycle detection, PageRank, Louvain) run on `FLOWS_TO`, because collapsing the 20-50 parallel `TRANSFER` edges between a pair into one edge keeps variable-length traversal from exploding combinatorially (branching = distinct neighbours, not parallel-edge count).

**Postgres** — canonical transaction records + outbox table (`pending_graph_sync` flag).

**Redis** — sorted sets keyed `edge:{node_a}:{node_b}`, scored by timestamp. Used for time-windowed volume queries (ZRANGEBYSCORE).

## Key patterns

**Dual-write consistency** — write to Postgres first with `pending_graph_sync = true`. Background outbox worker reads pending rows, writes to Neo4j, clears flag on success. Graph is eventually consistent with Postgres, never ahead of it. Retries use exponential backoff.

**Neo4j upserts** — use `MERGE` inside a transaction to atomically create nodes if missing. `TRANSFER` is MERGEd on `txn_id` (idempotent on outbox retry). `FLOWS_TO` is MERGEd on the account pair and its aggregates (`tx_count`, `total_amount`, min/max amount, first/last ts) are incremented on match. Never plain `CREATE`. Aggregate double-counting on `FLOWS_TO` is prevented by the outbox's once-delivery guarantee.

**Time-windowed queries** — do not hit Neo4j for volume in a time window. Use Redis ZRANGEBYSCORE on the relevant sorted set. Microsecond latency.

**Graph algorithms** — only run on subgraphs that received new edges in the processing window, not the full graph. Cycle detection traverses `FLOWS_TO` with a depth limit and time window: depth 6-8 and a 48h window for real-time/streaming; depth up to 12 with a wider window for batch/investigative sweeps (validated against IBM AML: 72% recall at depth 8, 87% at depth 12, both at 100% precision — see `benchmarks/ibm_aml/`). A per-account query timeout bounds worst-case latency so a deep search never stalls the pipeline. Cross-currency cycles and chains longer than the depth limit are the documented blindspots owned by FX normalization and the Louvain/PageRank detectors respectively.

**Community detection (Louvain/Leiden)** — daily batch over aggregate `FLOWS_TO` edges active in the last `LOUVAIN_WINDOW_DAYS` (default 30). Partitioning runs Python-side behind `LOUVAIN_ENGINE`: seeded `networkx.community.louvain_communities` by default (no extra dependency), or `leidenalg`/`igraph` Leiden as an opt-in engine that guarantees internally-connected communities and scales better (no GDS plugin either way). Edge weight is `log1p(total_amount)` (`LOUVAIN_WEIGHT_MODE`). Every community is split into connected components before scoring (fixes Louvain's disconnected-community defect). Communities are scored on five dimensions — size band, density, internal volume, isolation (`1 − conductance`), and overlap with accounts already flagged by other detectors — and those at/above medium persist to `risk_flags` as `flag_type='COMMUNITY'`, fingerprinted on their top-K weighted-degree core so daily re-detection upserts instead of duplicating. All kept communities are also written onto `Account` nodes as `community_id` / `community_detected_at`. Entrypoint: `python -m fraud.community_detector` (scheduling is a deploy concern — cron; not wired in-process). Untuned baseline on IBM AML non-cycle typologies (`benchmarks/ibm_aml/louvain_runner.py`): 81% recall overall (scatter-gather/gather-scatter/fan-in/fan-out 95–100%, bipartite/stack ~45%), but precision is low (~1%) until the overlap dimension (`--with-postgres`) and thresholds are tuned — that tuning is the documented follow-up.

**AI enrichment** — construct a natural-language subgraph summary, pass to Claude with `get_subgraph(account_id, depth, window)` tool, return structured output: `risk_level` (low/medium/high/critical), `confidence`, `explanation`. Explainability is a regulatory requirement, not optional.

## Conventions

- All graph writes go through the outbox — never write directly to Neo4j from the consumer without the Postgres record being committed first.
- The outbox rule applies to *payment events*. Batch algorithm jobs (cycle detection, Louvain) read Neo4j directly and may write derived analytical node properties (e.g. `community_id`) directly to Neo4j — never payment edges, and always with a `*_detected_at` provenance timestamp.
- Node type is always one of the four defined types. Do not invent new node types without updating the schema docs.
- Edge weights are always incremented, never overwritten — existing history must be preserved.
- Risk scores are always produced with a written explanation. Never emit a bare numeric score.
- Cypher queries use named parameters, never string interpolation.
- All time values stored and compared in UTC.

## Key queries (named API endpoints)

- `shortest_path_between(account_a, account_b)` — how two accounts are connected through intermediaries
- `subgraph_around(account_id, depth=3)` — full neighborhood up to N hops
- `flow_between(account_a, account_b, window='7d')` — total volume, path count, avg hop time in window