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

**In progress:**
- Storage layer (Postgres + Neo4j + Redis dual-write)

**Not started:**
- Graph algorithm engine
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

**Neo4j nodes** — types: `account`, `merchant`, `bank`, `exchange`. Properties: `kyc_tier`, `risk_score`, `country`, `account_age`, `cumulative_volume`.

**Neo4j edges** — directed. Properties: `total_amount`, `tx_count`, `first_seen`, `last_seen`, `avg_tx_size`.

**Postgres** — canonical transaction records + outbox table (`pending_graph_sync` flag).

**Redis** — sorted sets keyed `edge:{node_a}:{node_b}`, scored by timestamp. Used for time-windowed volume queries (ZRANGEBYSCORE).

## Key patterns

**Dual-write consistency** — write to Postgres first with `pending_graph_sync = true`. Background outbox worker reads pending rows, writes to Neo4j, clears flag on success. Graph is eventually consistent with Postgres, never ahead of it. Retries use exponential backoff.

**Neo4j upserts** — use `MERGE` inside a transaction to atomically create nodes if missing and increment edge weights. Never plain `CREATE`.

**Time-windowed queries** — do not hit Neo4j for volume in a time window. Use Redis ZRANGEBYSCORE on the relevant sorted set. Microsecond latency.

**Graph algorithms** — only run on subgraphs that received new edges in the processing window, not the full graph. Cycle detection DFS has a 6-hop depth limit and 48-hour time window.

**AI enrichment** — construct a natural-language subgraph summary, pass to Claude with `get_subgraph(account_id, depth, window)` tool, return structured output: `risk_level` (low/medium/high/critical), `confidence`, `explanation`. Explainability is a regulatory requirement, not optional.

## Conventions

- All graph writes go through the outbox — never write directly to Neo4j from the consumer without the Postgres record being committed first.
- Node type is always one of the four defined types. Do not invent new node types without updating the schema docs.
- Edge weights are always incremented, never overwritten — existing history must be preserved.
- Risk scores are always produced with a written explanation. Never emit a bare numeric score.
- Cypher queries use named parameters, never string interpolation.
- All time values stored and compared in UTC.

## Key queries (named API endpoints)

- `shortest_path_between(account_a, account_b)` — how two accounts are connected through intermediaries
- `subgraph_around(account_id, depth=3)` — full neighborhood up to N hops
- `flow_between(account_a, account_b, window='7d')` — total volume, path count, avg hop time in window