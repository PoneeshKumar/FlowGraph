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
- Storage layer (Postgres + Neo4j + Redis dual-write, outbox worker)
- Graph algorithm engine (cycle detection, PageRank, Louvain/Leiden)
- GNN layer — features, training, evaluation, inference. Trained model in
  `ml/runs/v2_derived_h128`; numbers and limitations in `ml/RESULTS.md`.

**Not started:**
- AI enrichment layer (Claude explainability for low-confidence cases)
- Query API (`shortest_path_between`, `subgraph_around`, `flow_between`)
- Frontend wiring (Vite app exists, not connected to a backend)

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

## AI / ML layer

Risk classification uses a trained GNN (GraphSAGE architecture, PyTorch Geometric).

- **Loss function**: Focal Loss (γ=2.0) to penalize missing the fraud class — `ml/losses.py`. Note α: the paper's 0.25 is a *binary* foreground weight, so as a scalar across 4 classes it only rescales the loss. Use a per-class α vector (`class_balanced_alpha`) for actual rebalancing.
- **Class imbalance**: SMOTE on **post-convolution embeddings**, never on raw node features — `ml/imbalance.py`. A synthetic account has no edges, so it appears nowhere in `edge_index` and cannot participate in message passing; interpolating raw feature vectors produces rows the GNN structurally cannot use. After the conv layers each row already encodes its neighbourhood, so interpolation is meaningful and needs no edges. The implementation stays in torch rather than calling imbalanced-learn, because a numpy round-trip severs autograd and the graph encoder would stop learning from synthetic samples. Try Focal Loss + per-class α alone first — it needs no synthetic data, and it is the baseline that shows whether SMOTE buys anything.
- **Node features**: 29 columns — `ml/features.py`. Structural aggregates off `FLOWS_TO` (in/out degree, volume, tx counts, net_flow, flow_ratio) + PageRank score + Louvain-derived community stats + Redis time-windowed volumes (1h / 24h / 7d) + node-type one-hot. The account properties below are **not** included: `create_account_node` is their only writer and nothing in production calls it, so they are null on every node. Add to `OPTIONAL_NODE_PROPERTY_FEATURES` once ingestion populates them.
- **Architecture**: SAGEConv layers for inductive learning — generalizes to unseen accounts
- **Labels**: weak labels from `risk_flags`, **`CYCLE` flags only** (`LABEL_FLAG_TYPES`). `COMMUNITY` flags are excluded to prevent target leakage: `score_community` derives `risk_level` from `risk_score` by fixed thresholds, and `community_risk_score` is itself a feature — so labelling from `COMMUNITY` hands the model its own answer. Louvain is a feature provider; cycle detection is the label source.
- **Evaluation**: never accuracy — at ~1% prevalence "all low risk" scores 99% and catches nothing. Precision/recall/PR-AUC on the fraud class, via `ml/evaluate.py`. Do **not** score the GNN against `risk_flags`: that only measures how well it imitates the detectors, and is blind wherever they are. Use the IBM AML ground truth (`benchmarks/data/HI-Small_Patterns.txt`, 370 attempts across 8 typologies, ~3,170 accounts). Only 54 attempts are CYCLE — recall on FAN-OUT / FAN-IN / BIPARTITE / STACK / GATHER-SCATTER / SCATTER-GATHER is the real test, since those have no closed loop and cycle detection cannot represent them at any depth.
- **Explainability**: natural language explanation generated from GNN output + subgraph structure (regulatory requirement)

The GNN is the primary classifier; cycle detection + Louvain detectors serve as feature providers and weak labels. Claude API is used only for secondary explainability in edge cases where GNN confidence is low.

## Data model

**Neo4j nodes** — types: `account`, `merchant`, `bank`, `exchange`. Properties: `kyc_tier`, `risk_score`, `country`, `account_age`, `cumulative_volume`.

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