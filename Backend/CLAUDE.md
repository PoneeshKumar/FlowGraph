# FlowGraph

Real-time money flow intelligence engine. Every payment is a directed edge in a live graph. The system detects fraud patterns — circular flows, hub-and-spoke networks, layering — that are invisible to row-based systems.

## Repo layout

This file lives in `Backend/`, but the **git root is one level up** (`FlowGraph/`):

- `Backend/` — the Python service: Faust consumer, storage clients, outbox worker, fraud engines, benchmarks, dev tools. Everything below refers to this directory unless stated otherwise.
- `Frontend/` — React 19 + Vite + Tailwind v4 + Motion SPA (liquid-glass design system). Four views — Dashboard, GraphExplorer, AlertsView, TransactionsView — currently running entirely on `src/data/mockData.js`. Not wired to any backend API yet (no API exists).
- `docker-compose.yml` (root) — Kafka (KRaft, single broker, 6 partitions, topic auto-create **off**), Neo4j 5, Redis 7, Postgres 16, pgAdmin on :5050.
- `context.md` / `outline` (root) — the original layer-by-layer vision narrative. This file supersedes them where they disagree.

## Architecture

Five layers, top to bottom:

```
Payment events → Kafka → Python consumer (Faust)
                              ↓            ↓            ↓
                          Postgres      Neo4j        Redis
                         (outbox)      (graph)      (ZSET)
                                          ↓
                              Cycle detection (Cypher over FLOWS_TO)
                              Incremental PageRank            [not built]
                              Louvain clustering (daily batch)
                                          ↓
                              Risk flag store (risk_flags)
                              Risk flag aggregator             [not built]
                                          ↓
                              Claude API (subgraph summary → risk classification)  [not built]
                                          ↓
                              FastAPI (Cypher-over-HTTP)       [not built]
                              React force-directed graph UI    [shell built, mock data]
```

## Current status (2026-07-05)

**Branch geography:** `cycle-detection` (PR #13, open) carries the TRANSFER/FLOWS_TO edge model, `risk_flags`, the cycle engine, and the IBM AML benchmark harness. `louvain-clustering` stacks community detection on top of it. `origin/kafka-layer` (unmerged) adds a crypto consumer to the Faust app. `main` has ingestion models, storage layer, and the frontend shell.

**Built and verified:**
- Event models for all four rails (`models/`): card auth/settlement (PAN hashing, auth↔settlement match validator), ACH credit/debit (SEC codes, trace numbers), wire (domestic/international rails, SWIFT/Fedwire/SEPA formats), crypto (BTC/ETH wallet validation, confirmation thresholds). All extend `BasePaymentEvent` (UTC-enforced timestamps, integer cents, `amount_usd_cents`).
- Synthetic generators for all four rails (`generator/`) — Poisson arrivals into `payments.raw.*`.
- Faust consumer for the **card** rail (`consumer/faust_app.py`): normalize → validate → atomic Postgres write (transaction + outbox) → malformed events to DLQ. Partition key `sender_id`.
- Outbox sync worker (`worker/outbox_sync_worker.py`): polls Postgres, writes Neo4j + Redis, marks synced; linear retry backoff, capped retries, then `failed`.
- Neo4j graph writes (`db/neo4j.py`): per-payment `TRANSFER` + aggregate `FLOWS_TO` edges, constraints at startup.
- Cycle detection (`fraud/cycle_detector.py` + `Neo4jClient.find_cycles`) — benchmarked on IBM AML (see Benchmarks).
- Louvain community detection (`fraud/community_detector.py`) — daily batch, networkx/Leiden — benchmarked on IBM AML.
- Dev graph visualiser (`tools/visualize_neo4j.py`) — self-contained HTML force-directed view of FLOWS_TO, coloured by `community_id`.

**In progress / known gaps:**
- Storage-layer wiring: `main.py` starts DB clients + outbox worker + metrics but **never launches the Faust worker** (its docstring claims otherwise). Components are individually tested; the single-process live path consumer→Postgres→outbox→Neo4j/Redis has not run end-to-end.
- Only the card rail has a normalizer and consumer agent. Cycle detection on live traffic needs an account→account rail (wire/crypto) consumer.
- `amount_usd_cents` exists on the models but is not persisted to Postgres nor forwarded through the outbox payload.
- FX conversion (`fx.py`) is a 1:1 stub.

**Not built yet:** incremental PageRank, risk flag aggregator, AI enrichment, query API, frontend↔backend wiring. Design intent for each is in "Roadmap" below — do not treat those sections as existing code.

## Stack

| Component | Technology |
|---|---|
| Message queue | Kafka (KRaft, no Zookeeper) |
| Stream processing | Python 3.9 / Faust 1.10.4 |
| Graph database | Neo4j 5 (async driver, no GDS plugin) |
| Relational store | Postgres 16 (asyncpg) |
| Cache / time-series | Redis 7 (ZSET) |
| Graph algorithms | networkx 3.2.1 (pinned); optional igraph+leidenalg (GPL, opt-in) |
| API (planned) | FastAPI |
| Frontend | React 19 + Vite + Tailwind v4 + Motion |
| AI enrichment (planned) | Claude API (claude-sonnet-4-6) |

**Python 3.9.6 constraint** (faust pins it): no `match`, no runtime `X | Y` unions, networkx must stay 3.2.1, new modules use `from __future__ import annotations`. If GDS is ever adopted, `graphdatascience==1.17` is the last Py3.9 release.

## Running things

```bash
docker compose up -d                       # from repo root: Kafka, Neo4j, Redis, Postgres, pgAdmin
psql ... -f migrations/001_*.sql -f migrations/002_*.sql   # no migration runner — apply manually
python main.py                             # DB clients + outbox worker + metrics (NOT the Faust consumer — see gaps)
python -m generator.card_generator         # synthetic events (also ach/wire/crypto); topics are NOT auto-created
python -m fraud.cycle_detector             # seeds a demo A→B→C→A ring, runs detection (applies migration 002 itself)
python -m fraud.community_detector         # seeds a gather-scatter demo, runs the Louvain batch
python -m tools.visualize_neo4j --open     # HTML graph of communities; --community <id>, --prefix DEMO_LV_, --all
pytest                                     # from Backend/ (or `python run_tests.py` from repo root)
```

Tests use markers `unit` / `integration` / `e2e`; real-Neo4j integration tests self-skip when Neo4j is unreachable. `.env.example` documents the compose credentials; all tunables are env vars with documented defaults in `config.py` (`CYCLE_*`, `LOUVAIN_*`, `OUTBOX_*`).

## Data model

**Neo4j nodes** — schema allows four types: `account`, `merchant`, `bank`, `exchange` (planned properties: `kyc_tier`, `risk_score`, `country`, `account_age`, `cumulative_volume`). The payment write path currently creates only `:Account {id}` nodes. The Louvain batch adds `community_id` (12-hex-char) and `community_detected_at` (epoch seconds) to `Account` nodes.

**Neo4j edges** — two directed edge types are maintained per payment:
- `TRANSFER` — one edge per transaction, keyed by `txn_id` (MERGE key). Properties: `amount_cents`, `ts` (unix seconds), `rail`, `event_type`. Full per-transaction detail; used for audit and per-txn tracing.
- `FLOWS_TO` — one aggregate edge per directed account pair. Properties: `tx_count`, `total_amount`, `first_ts`, `last_ts`, `min_amount`, `max_amount`, `rail`. Graph algorithms (cycle detection, PageRank, Louvain) run on `FLOWS_TO`, because collapsing the 20-50 parallel `TRANSFER` edges between a pair into one edge keeps variable-length traversal from exploding combinatorially (branching = distinct neighbours, not parallel-edge count).

**Postgres** (`migrations/`):
- `transactions` — canonical record of every event (source of truth): rail, event_type, status (`PENDING/SETTLED/DECLINED/ORPHANED`), hashed sender/receiver, `amount_cents`, currency, `timestamp_utc`, `raw_payload` JSONB, `authorization_code` for auth↔settlement matching.
- `outbox` — one row per transaction, `status` pending/synced/failed, `idempotency_key` UNIQUE (sha256 of event_id+timestamp), retry bookkeeping.
- `risk_flags` — shared store for **all** detectors, discriminated by `flag_type` (`CYCLE`, `COMMUNITY`; `STRUCTURING`/`CTR` anticipated). `fingerprint` UNIQUE → re-detection upserts (bumps `detection_count`, refreshes assessment) instead of duplicating alerts. `explanation` NOT NULL (regulatory). `status` open/reviewed/dismissed/escalated anticipates an analyst workflow.

**Redis** — sorted set per directed edge, key `edge:{sender}:{receiver}`, member `"{amount_cents}|{unix_ts}"`, score = unix ts, 30-day TTL. Answers time-windowed volume questions via ZRANGEBYSCORE in microseconds; also in-degree and above-threshold scans.

## Key patterns

**Dual-write consistency (outbox)** — write to Postgres first (transaction + outbox row atomically). The background worker polls pending outbox rows (5s interval, batch 50), writes Neo4j then Redis, marks synced. Graph is eventually consistent with Postgres, never ahead of it. Retries use **linear** backoff (`retry_count × 10s`, max 3) then mark `failed`.

**Neo4j upserts** — use `MERGE` inside a transaction to atomically create nodes if missing. `TRANSFER` is MERGEd on `txn_id` (idempotent on outbox retry). `FLOWS_TO` is MERGEd on the account pair and its aggregates (`tx_count`, `total_amount`, min/max amount, first/last ts) are incremented on match. Never plain `CREATE`. Aggregate double-counting on `FLOWS_TO` is prevented by the outbox's once-delivery guarantee.

**Time-windowed queries** — do not hit Neo4j for volume in a time window. Use Redis ZRANGEBYSCORE on the relevant sorted set.

**Graph algorithms** — only run on subgraphs that received new edges in the processing window, not the full graph. Every algorithm query carries a transaction timeout so a runaway search can never stall the pipeline (per-account budget for cycles, larger bounded budget for batch exports).

**Batch-write exception to the outbox rule** — the outbox applies to *payment events*. Batch algorithm jobs (cycle detection, Louvain) read Neo4j directly and may write derived analytical node properties (e.g. `community_id`) directly to Neo4j — never payment edges, and always with a `*_detected_at` provenance timestamp. They MATCH (not MERGE) so a deleted account is never resurrected by an algorithm write.

## Fraud detection engines

Both engines share a shape: pure, I/O-free scoring + fingerprinting functions (unit-testable) orchestrated by a detector class; every flag is scored 0–1, mapped to low/medium/high/critical, given a written explanation, and upserted into `risk_flags` idempotently on its fingerprint.

**Cycle detection** (`fraud/cycle_detector.py`) — finds circular flows A→B→…→A via variable-length Cypher over `FLOWS_TO`, then Python-side filters: simple-cycle check (no repeated intermediates) and canonical ring fingerprint (rotate to lex-smallest, sha256) so the same ring seeded from N accounts yields one flag. Temporal model is rotation-invariant: at most one time-descent around the ring (the wrap point), consecutive-hop gap ≤ `CYCLE_MAX_HOP_GAP_HOURS`. Conservation modes (`CYCLE_CONSERVATION_MODE`): `hop` (per-hop min/max envelope overlap, cross-currency hops skipped — default, best F1), `cycle` (whole-ring magnitude consistency), `off`. Scoring: 0.30·value + 0.35·velocity + 0.25·amount-consistency + 0.10·hop-count. Config defaults are the streaming posture (depth 6, 48h window); batch sweeps raise depth to 12 with a wider window. Not yet triggered by live traffic — manual/test entrypoint only.

**Louvain community detection** (`fraud/community_detector.py`) — daily batch over aggregate `FLOWS_TO` edges active in the last `LOUVAIN_WINDOW_DAYS` (default 30). Catches the shapes cycle detection is blind to: gather-scatter, scatter-gather, fan-in/out, bipartite, stacks. Partitioning runs Python-side behind `LOUVAIN_ENGINE`: seeded `networkx.community.louvain_communities` by default, or `leidenalg`/`igraph` Leiden as opt-in (internally-connected communities by construction, scales better; GPL). Edge weight `log1p(total_amount)` (`LOUVAIN_WEIGHT_MODE`) — value-aware but whale-dampened. Every community is split into connected components before scoring (fixes Louvain's disconnected-community defect). Scored on five dimensions — size band (0.10), density (0.15), internal volume (0.25), isolation = 1−conductance (0.15), and overlap with accounts flagged by *other* detectors (0.35; excludes its own flags to avoid a feedback loop). Communities ≥ medium persist to `risk_flags` as `flag_type='COMMUNITY'`; fingerprint = sha256 of top-K weighted-degree core (stable under peripheral churn; core split/merge spawns a new flag — accepted blindspot). All kept communities get `community_id` node properties. Scheduling is a deploy concern (cron) — not wired in-process.

## Benchmarks (`benchmarks/ibm_aml/`)

Validation against the Kaggle **IBM AML HI-Small** dataset (`HI-Small_Trans.csv` + `HI-Small_Patterns.txt` in `benchmarks/data/`, gitignored — download manually). The ingestor writes through the real production writer (`upsert_transaction_graph`), no special-cased path. Detection anchors to the dataset's own max timestamp, so window envs must cover the ~30-day span (`CYCLE_WINDOW_HOURS=720`, `LOUVAIN_WINDOW_DAYS=60`).

- **Cycle** (`runner.py`, 54 labeled CYCLE groups, realistic noise: ring accounts carry ~45% legitimate edges): depth 8 / gap 168h / `hop` conservation → **72.2% recall, 100% precision, F1 83.9, ~24s** (real-time posture). Depth 12 → **87.0% recall, 100% precision, F1 93.1, ~2–15min** (batch posture). The residual ~13% miss = cross-currency cycles (owned by FX normalization) and >12-hop chains (owned by Louvain/PageRank) — structural blindspots, not tuning knobs. `blindspots.py` attributes every miss to the first filter that excluded it.
- **Louvain** (`louvain_runner.py`, non-cycle typologies, untuned baseline): **81.3% recall overall** (scatter-gather/gather-scatter/fan-in/fan-out 95–100%, bipartite/stack ~45%), **precision ~0.7%** until the overlap dimension (`--with-postgres`, run after the cycle benchmark) and thresholds are tuned — that tuning is the documented follow-up. No external baseline exists for these typologies on HI-Small; this runner is the reference number.

## Roadmap (not built — design intent)

- **Incremental PageRank** — hub/centrality score as a node property, recomputed only for the local subgraph around nodes that received new edges. Together with Louvain it owns the >depth-limit chain blindspot. Separate plan when started.
- **Risk flag aggregator** — converge cycle + hub + community signals into one per-account risk signal. `risk_flags` (with its `flag_type` discriminator and `status` workflow) is the substrate; the aggregator reads it, not raw detector output.
- **AI enrichment** — build a natural-language subgraph summary, give Claude a `get_subgraph(account_id, depth, window)` tool (a building block already exists as `Neo4jClient.get_subgraph`), return structured output: `risk_level` (low/medium/high/critical), `confidence`, `explanation`. Every high/critical flag renders a compliance report (account, triggering signals, subgraph summary, reasoning). Explainability is a regulatory requirement, not optional.
- **Query API** — FastAPI, Cypher-over-HTTP plus the named endpoints below; WebSocket push for live graph updates to the frontend.
- **Frontend wiring** — replace `mockData.js` with the query API; node colour = risk tier, edge thickness = volume; click-to-expand via `subgraph_around`.
- **Remaining rails** — wire/ACH/crypto normalizers + consumer agents (crypto consumer exists unmerged on `origin/kafka-layer`). Wire/crypto unlock live cycle detection (account→account edges). Auth↔settlement matching (validator + `ORPHANED` status exist) is not yet a pipeline step.
- **Real FX** — replace the `fx.py` 1:1 stub with rate-at-transaction-time; persist `amount_usd_cents` end-to-end. Unlocks cross-currency cycle detection.
- **Louvain follow-ups** (see `docs/superpowers/plans/2026-07-03-louvain-clustering.md` for the full list): cron wiring, knob tuning against the baseline, temporal-burstiness 6th scoring dimension (from the Redis ZSETs), Jaccard flag-matching if core churn duplicates alerts, server-side `gds.leiden` if graph-in-memory becomes the ceiling, AMLGentex as an external scenario generator.

## Conventions

- All graph writes for payment events go through the outbox — never write directly to Neo4j from the consumer without the Postgres record being committed first. Batch algorithm jobs are the documented exception (derived node properties only, with provenance timestamps).
- Node type is always one of the four defined types. Do not invent new node types without updating the schema docs.
- Edge weights are always incremented, never overwritten — existing history must be preserved.
- Amounts are integer cents everywhere; never floats. Cross-currency comparisons are invalid until FX lands — code that compares amounts must either stay same-currency or skip (see cycle conservation).
- Risk scores are always produced with a written explanation. Never emit a bare numeric score (`upsert_risk_flag` raises without one).
- Cypher queries use named parameters, never string interpolation. (Literal ints that Cypher requires inline, like variable-length bounds, come from config — never user input.)
- All time values stored and compared in UTC; Neo4j edge timestamps are unix epoch **seconds** (int), never ms.
- Detector identity is a fingerprint: canonicalize (ring rotation / community core), sha256, upsert. New detectors should follow the pure-scoring + fingerprint + `risk_flags` shape.
- Every algorithm query gets a transaction timeout. A fraud query must never hang the pipeline; a timed-out search is treated as "no finding".
- New tunables are env-var knobs in `config.py` with documented defaults, following the `CYCLE_*` / `LOUVAIN_*` style.

## Key queries (planned API endpoints)

- `shortest_path_between(account_a, account_b)` — how two accounts are connected through intermediaries (`Neo4jClient.shortest_path` exists)
- `subgraph_around(account_id, depth=3)` — full neighborhood up to N hops (`Neo4jClient.get_subgraph` exists)
- `flow_between(account_a, account_b, window='7d')` — total volume, path count, avg hop time in window (Redis-backed)
