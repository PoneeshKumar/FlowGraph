# Showcase & Ship — Design Spec

- **Date:** 2026-09-15
- **Status:** Approved (design) — pending implementation plan
- **Author:** KavEn06 (with Claude)
- **Branch:** `showcase-and-ship` (off `main` at `8b787e9`, which contains the merged
  `live-per-event-scoring` work)

## 1. Summary

Finish FlowGraph as a **publishable, usable project**: a React frontend that
showcases everything the backend can do, running against real data; a public
demo on Vercel that costs nothing to host and never goes down; a README that
lets a stranger understand and run it; and a way for people to run the engine
on their own data.

Five things are built, in this order:

1. **Backend read API** so the three mock views (Dashboard, Alerts, Transactions)
   can show real data, plus fixes to the graph service.
2. **Frontend wiring + a native Pipeline view**, with a data-source layer that
   serves either a live backend or a bundled snapshot.
3. **"Analyze a file"** — upload a transaction CSV and run the whole pipeline on it.
4. **Snapshot generator + demo mode** — precomputed JSON that makes the Vercel
   build fully interactive with no backend.
5. **README, docs, CI, deploy config.**

Plus a **pluggable explanation service** (local Ollama model by default, Claude
API opt-in, rule-based fallback) replacing today's stub and the broken agent.

## 2. Context — what actually exists (measured, not assumed)

| Fact | Consequence for this design |
|---|---|
| Postgres has **no** `transactions` / `outbox` tables — the graph was bulk-loaded by `ml/datasets/run_ingest.py` straight into Neo4j + Redis. Postgres holds `risk_flags` (11,878 rows: AGGREGATE 9,776 · COMMUNITY 1,981 · CYCLE 111 · LIVE_GNN 10) and `pipeline_runs`. | Transaction-level reads come from Neo4j `TRANSFER` edges, not Postgres. |
| **5,044,315 `TRANSFER` edges** (`txn_id, event_type, amount_cents, rail, ts, created_at`), **`ts` spans 1661990400–1663517880 (2022-09-01 → 2022-09-18, 18 days)**. `rail` is `IBM_AML_<currency>` — 15 currencies (US Dollar 1.9M, Euro 1.17M, …). No index on `ts`; a full scan is ~8 s. | Aggregate stats are computed **once** and cached; a relationship index on `TRANSFER(ts)` makes "latest N" instant. Time windows are anchored to the **dataset's end**, never wall-clock. Amounts are **not FX-normalised** → the UI selects one currency at a time. |
| Account nodes carry `id, gnn_risk_score, gnn_risk_tier, community_id, pagerank_score, created_at, pagerank_updated_at, community_detected_at`; `in_cycle` on 74 nodes. `risk_score/risk_tier/label/node_type/business_id` exist only on 13 mock seed accounts (`acc_*`, from `scripts/seed_mock_data.py`, timestamps in 2026). | `GraphService` reads `risk_score` → every real account renders "low" (**bug**). Fix by coalescing to the GNN score. Purge the 13 mock nodes before snapshotting. |
| Redis holds **11 keys** — the mock seed only. Edge ZSETs for the real graph were never populated. | `/graph/flow` (Redis-backed) is dead on real data → reimplement over `TRANSFER`. |
| `AIEnrichmentService` is an honest stub. `app/agent/tools.py` targets Ollama via `pydantic_ai` but uses `int \| None` syntax; **every interpreter on this machine is Python 3.9.6** (system and `.venv`), so it fails on import (swallowed by a bare `except`). The business-summary endpoint matches `business_id`, which only the 13 mock accounts have. | Replace both with one `ExplanationService`. All new backend code targets **Python 3.9** (`Optional[X]`, `List[X]`, no `X \| None`, no `match`). |
| GNN artifacts present locally: `ml/runs/v10_L3`, `v10_L3_s1`, `v10_L3_s7`, `ml/cache/featureset_v4.npz`. Ground truth `benchmarks/data/HI-Small_Patterns.txt` present (tracked). `ml/runs`, `ml/cache` gitignored. | The pipeline works here; strangers need a fetch script for the checkpoints. |
| Six ~94 MB CSV chunks (`HI-Small_Trans.csv.part-*`) are **tracked** in git despite the ignore rule (~560 MB). | Out of scope to rewrite history; flagged in §12 for a follow-up PR. |
| Root `.gitignore` ignored `*.md`, `*.MD` and every `data/` dir. | Fixed in WP0 (this spec could not otherwise be committed). |
| Frontend: Vite + React 19 + Tailwind 4 + `motion` + Cytoscape; a light "Liquid Glass Ledger" design system (`src/index.css`, `src/components/ui.jsx`). `GraphExplorer`, `GraphCanvas`, `InspectorSidebar` are styled for a **dark** slate theme — inconsistent with the rest. Dashboard/Alerts/Transactions run on `src/data/mockData.js`. Sidebar shows a fake "Live · 847/s · 42ms lag" strip and a hard-coded badge. | Restyle the graph views to the light system; replace fake chrome with real mode/state. |
| `/viz` router already exposes JSON for the whole pipeline (`/overview`, `/communities`, `/subgraph`, `/marked`, `/threshold`, `/metrics`, `/run`, `/run/latest`, `/run/{id}`) and `PipelineRunner` runs pagerank → louvain → cycle → gnn → aggregate with progress in `pipeline_runs`. **The GNN stage scores from the cached IBM `.npz`, not from the live stores.** | The native Pipeline view reuses these endpoints. The upload path must **rebuild features live** via `FeatureBuilder`. |
| Docker: `flowgraph-neo4j`, `flowgraph-postgres`, `flowgraph-redis` up. The API is **not** running; root `config.py` defaults to Postgres password `flowgraph` while the container uses `changeme`. | Run the API with `POSTGRES_DSN=postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph`. Document it. |
| No CI. No `vercel.json`. `Frontend/README.md` is Vite boilerplate. Root README (397 lines) has no images or diagrams and says the frontend "is not yet wired". | WP5. |
| Hardware: Apple M4, 16 GB. Ollama not installed. No Anthropic credentials on the machine. | Local model is viable; nothing is assumed installed. |

## 3. Resolved requirements

| Decision | Answer |
|---|---|
| Scope | Full showcase: real data in every view **and** a native Pipeline view |
| Pipeline UI | Rebuilt natively in React over the existing `/viz` JSON (the server-rendered `/viz` page stays as-is) |
| Deployment | **Model A** — static demo on Vercel fed by a bundled snapshot; self-host for live use. Live mode is an env-var switch so a hosted backend later is config only |
| Own data | "Analyze a file" — **replace** the current graph (sandbox), one dataset at a time, live mode only |
| AI explanations | One `ExplanationService` with providers `ollama` (default when reachable), `anthropic` (opt-in, key required), `none` (rule-based). Snapshot explanations are generated once locally with whichever provider is chosen |
| Currency | Per-currency stats with a selector (default US Dollar); no cross-currency summing |
| Time | All periods anchored to the dataset's last timestamp: **24H / 7D / All** |
| Design | Every new/changed screen uses the existing light design system |
| Python | 3.9-compatible backend code |

## 4. Non-goals

- Multi-dataset namespacing / dataset switching without reset.
- Hosted public backend (model B), authentication, rate limiting.
- In-browser models (WebLLM) for demo visitors — the snapshot is strictly better.
- FX normalisation across currencies.
- Websockets — progress is polled (as `/viz` already does).
- Rewriting git history to remove the tracked CSV chunks.
- Retraining the GNN; changing feature columns (column order is a model contract).

## 5. Architecture

```
Frontend (Vite/React) ──VITE_API_BASE_URL set──▶ FastAPI /api  ──▶ Neo4j · Postgres · Redis
       │                                             │
       │  unset (Vercel)                             ├─ /api/pipeline/*  (aliases of /viz/* JSON)
       ▼                                             ├─ /api/datasets/*  (upload → DatasetRunner)
  src/data/snapshot/*.json  ◀── scripts/export_snapshot.py  (runs locally against the stores)
```

New backend modules:

```
app/services/stats_service.py        # StatsCache: one-pass TRANSFER histogram + counts; series math (pure)
app/services/alerts_service.py       # risk_flags list/count/status update (thin)
app/services/transactions_service.py # TRANSFER reads (latest N / per account), tier + flagged join
app/services/explanation_service.py  # AccountEvidence (pure) + providers: ollama | anthropic | none
app/services/dataset_runner.py       # upload job: reset → ingest → live features → pipeline
app/api/endpoints.py                 # extended: /stats, /alerts, /transactions, /datasets; agent endpoint removed
app/api/main.py                      # viz api sub-router mounted at /viz and /api/pipeline; StatsCache warm-up
db/neo4j.py                          # init_constraints: + TRANSFER(ts) relationship index
db/postgres.py                       # get_risk_flags(offset), count_risk_flags, update_risk_flag_status, app_meta get/set
migrations/004_create_app_meta_table.sql
scripts/export_snapshot.py           # writes Frontend/src/data/snapshot/
scripts/purge_mock_seed.py           # removes the 13 acc_* seed nodes + their Redis keys
scripts/fetch_models.py              # downloads checkpoint + feature cache from the GitHub Release
scripts/plot_results.py              # results charts (PNG) for the README
```

Removed: `app/agent/` (both files), `POST /api/agent/business-summary`,
`BusinessSummaryRequest/Response`, `GraphService.get_business_risk_summary`,
`GraphService.get_risky_accounts` (only the agent used them), `pydantic_ai`
from requirements.

New frontend modules:

```
src/services/dataSource.js          # interface + createLiveSource(baseUrl) + createSnapshotSource()
src/services/DataSourceProvider.jsx # context: {source, mode:'live'|'demo', meta, retryLive}
src/router.js                        # tiny hash router (#/overview, #/graph, #/alerts, #/transactions, #/pipeline, #/upload)
src/components/PipelineView.jsx     # + PipelineControls, ThresholdPanel, LensTabs, CommunitiesTable, MarkedTable
src/components/UploadView.jsx
src/components/GraphTools.jsx        # shortest-path + flow tools for GraphExplorer
src/components/ModePill.jsx          # Live / Demo indicator in the Sidebar
src/data/snapshot/                   # generated JSON (committed; ≤ 4 MB)
```

Deleted: `src/data/mockData.js` (after every consumer is wired).

## 6. Backend API contracts (all under `/api`, read-only unless noted)

### 6.1 `GET /stats/overview`

```json
{
  "dataset": {"name": "IBM AML HI-Small", "source": "ibm-hi-small", "start_ts": 1661990400,
              "end_ts": 1663517880, "labelled": true},
  "accounts": 514002, "flows": 1010396, "transactions": 5044315,
  "scored_accounts": 513989, "communities": "<count of distinct community_id — measured at build time>", "in_cycle": 74,
  "open_flags": {"AGGREGATE": 9776, "COMMUNITY": 1981, "CYCLE": 111, "LIVE_GNN": 10, "total": 11878},
  "currencies": [{"code": "US Dollar", "rail": "IBM_AML_US Dollar", "tx_count": 1895167, "volume": 1.23e11}, "…"],
  "latest_run": {"id": "…", "status": "completed", "stage": "aggregate", "progress": 1.0,
                 "counts": {}, "started_at": "…", "finished_at": "…"},
  "computed_at": 1757900000
}
```

`volume` is in **major units** of that currency (`amount_cents / 100`). `code` strips an
`IBM_AML_` prefix; any other rail string is used verbatim. `communities` = distinct
`community_id`. `dataset` comes from the `app_meta` row `dataset` (seeded to the IBM
values by migration 004; rewritten by an upload).

### 6.2 `GET /stats/volume-series?currency=<code>&period=24h|7d|all`

```json
{"currency": "US Dollar", "period": "7d", "anchor_ts": 1663517880, "start_ts": 1662913080,
 "bucket_seconds": 21600,
 "t": [1662913080, "…"], "volume": [0.0, "…"], "txns": [0, "…"], "baseline": [0.0, "…"]}
```

- `volume[i]` — **cumulative** volume (major units) from `start_ts` to bucket *i*; `txns[i]` cumulative count.
- `baseline[i] = volume[-1] * i / (n-1)` — the *uniform-pace* line (what cumulative
  volume would be if flow were constant). This is what the dashed line means.
- Buckets: `24h` → 1800 s (48 pts); `7d` → 21600 s (28 pts); `all` → 86400 s (dataset span).
- `period=all` uses `start_ts = dataset.start_ts`.
- 404 for an unknown currency; 422 for a bad period.

### 6.3 `StatsCache` (in-process)

One Cypher pass builds the base histogram — `(rail, ts // 1800) → (count, sum(amount_cents))` —
~13k rows for the IBM graph. Every series/period/currency derives from it in memory (coarser
buckets are sums of finer ones). Counts use the count store; `communities` and `open_flags`
are one scan / one SQL each. Built by a background task at app startup (endpoints return
`503 {"detail": "stats warming up"}` until ready — the frontend shows a skeleton and retries),
and `invalidate()`d by `PipelineRunner._aggregate` and `DatasetRunner`. Series math lives in
pure functions (`bucketize`, `cumulate`, `uniform_baseline`) and is unit-tested.

### 6.4 `GET /alerts?flag_type=&min_level=low&status=open&limit=100&offset=0`

```json
{"total": 9776, "items": [{
  "id": 123, "flag_type": "AGGREGATE", "risk_level": "high", "risk_score": 0.81,
  "explanation": "…", "account_ids": ["…"], "primary_account": "…",
  "details": {"signals": {"gnn": true, "cycle": false}, "gnn_score": 0.81, "community_id": "…"},
  "status": "open", "first_detected_at": "…", "last_detected_at": "…", "detection_count": 3}]}
```

Backed by `pg.get_risk_flags(flag_type, min_level, status, limit, offset)` (add `offset`)
and new `pg.count_risk_flags(same filters)`. `limit ≤ 500`.

### 6.5 `PATCH /alerts/{id}/status` — body `{"status": "open|reviewed|dismissed|escalated"}`

Returns the updated row. 404 if unknown, 422 on a bad status. New `pg.update_risk_flag_status`.

### 6.6 `GET /transactions?limit=50&account_id=&currency=`

```json
{"items": [{
  "txn_id": "…", "sender_id": "…", "receiver_id": "…", "amount_cents": 369734,
  "currency": "US Dollar", "rail": "IBM_AML_US Dollar", "event_type": "SETTLEMENT", "ts": 1661991600,
  "sender_tier": "low", "receiver_tier": "high", "risk_tier": "high", "flagged": true}]}
```

- Global: `MATCH ()-[t:TRANSFER]->() … ORDER BY t.ts DESC LIMIT $limit` — requires the
  new relationship index `transfer_ts` (`CREATE INDEX transfer_ts IF NOT EXISTS FOR ()-[t:TRANSFER]-() ON (t.ts)`,
  added to `init_constraints`, which `viz_deps.startup()` now calls).
- Per account: both directions from the node, `ORDER BY t.ts DESC LIMIT`.
- `risk_tier = max(sender_tier, receiver_tier)` on the ladder low<medium<high<critical.
- `flagged` = either party is in the open-AGGREGATE set (cached in `StatsCache`, ~10k ids).
- `currency` filters on the rail. `limit ≤ 200`.

### 6.7 Graph service fixes (existing endpoints, same paths)

`NodeData` gains `gnn_risk_score`, `gnn_risk_tier`, `in_cycle`, `marked` (= `threshold.is_marked`).
`risk_score = coalesce(a.risk_score, a.gnn_risk_score, 0)`; `risk_tier = coalesce(a.risk_tier, a.gnn_risk_tier, mapped)`.
Applied to `get_subgraph` and `get_shortest_path`. `get_flow_between` reads
`(a)-[t:TRANSFER]->(b)` with `t.ts >= anchor - window` where `anchor = StatsCache.dataset.end_ts`
(falls back to now). Response shape unchanged. Also `GET /graph/account/{id}` → the single node's
`NodeData` (the Inspector needs it without a subgraph fetch).

### 6.8 `/api/pipeline/*`

The `/viz` router is split into `pages` (the `/` HTML index) and `api` sub-routers; `api` is
included at both `/viz` and `/api/pipeline`. New in `api`:
`GET /metrics/curve?points=46` → `{"loaded": true, "cutoffs": [0.50, …0.95], "precision": […], "recall": […], "fp": […], "tp": […], "marked": […]}`
(a loop over `metrics.confusion_at`; `loaded:false` when the graph is unlabelled).

### 6.9 `GET /accounts/{id}/enrich` (existing path, new implementation)

```json
{"account_id": "…", "risk_level": "high", "confidence": 0.78, "explanation": "…",
 "detected_typology": "FAN-IN", "compliance_summary": "…",
 "provider": "ollama", "model": "llama3.2", "generated_at": 1757900000}
```

Never errors for lack of a model: `provider:"none"` returns the rule-based text.

### 6.10 Datasets

- `GET /datasets/current` → the `app_meta.dataset` row.
- `GET /datasets/template` → `text/csv` with the required header row.
- `POST /datasets/upload` (multipart: `transactions` required, `patterns` optional, field
  `confirm` must equal `"replace"`) → `{"run_id": "…"}`; 409 if a run is active; 413 over
  `UPLOAD_MAX_MB` (default 500); 422 if the first 5 data rows do not parse via the ingest
  row parser. Files are streamed to `Backend/uploads/<run_id>/` (gitignored). Progress is
  polled from the existing `/pipeline/run/{id}`; stages for this job are
  `reset, ingest, pagerank, louvain, cycle, gnn, aggregate`.

**`DatasetRunner`** wraps: `reset_stores` (moved from `run_ingest._reset_stores` into
`ml/datasets/ibm_aml.py` as a public function; also truncates `risk_flags`) → `ingest_for_training`
(with the Redis client, so volume features are real for uploads) → `truth.reload(patterns_path)`
(new; empty set when absent) → `PipelineRunner.run(run_id, feature_source="live")`. With
`feature_source="live"` the GNN stage calls `FeatureBuilder(neo4j, redis, pg).build(reference_time=max_ts, window_days=span+1)`,
saves the result to `ml/cache/featureset_upload.npz`, and points the runner's effective cache
at it, so later plain "Run pipeline" runs stay fast. `app_meta.dataset` is rewritten
(`source:"upload"`, `name` = filename, `labelled` = patterns given, `rows` = ingest stats).
`StatsCache`, `metrics`, `threshold` are invalidated. Column contract: the IBM positional
layout (`Timestamp, From Bank, Account, To Bank, Account, Amount Received, Receiving Currency, Amount Paid, Payment Currency, Payment Format, Is Laundering`).

## 7. Explanation service

`explain(account_id) -> Explanation` in three steps:

1. **Evidence** (`build_evidence(node, neighbours, flags, dataset) -> AccountEvidence`, pure,
   unit-tested): node signals (GNN score/tier, in-cycle, community id/size/avg risk, PageRank
   percentile), top-5 payers and payees with amounts/counts, open flags with their stored
   explanations, dataset context (labelled or not; typology if labelled — **only in the snapshot
   generator, never sent to a live model as a hint**).
2. **Provider** produces JSON matching `ExplanationOut` (Pydantic): `risk_level` ∈ low/medium/high/critical,
   `confidence` ∈ [0,1], `explanation` (≤ 120 words), `detected_typology` (one of the 8 or null),
   `compliance_summary` (one sentence).
   - `anthropic`: `anthropic.AsyncAnthropic()`, model `LLM_MODEL` (default `claude-opus-5`),
     structured output via `output_config.format` with the JSON schema, stable system prompt
     with `cache_control` so repeated calls hit the cache, `max_tokens` 1024.
   - `ollama`: `ollama.AsyncClient(host=OLLAMA_BASE_URL).chat(model=LLM_MODEL or "llama3.2", format=<schema>, messages=…)`.
   - `none`: deterministic text from the aggregator rationale / stored flag explanation.
3. **Validate**; on any provider error or schema failure → `none` result (logged), never a 5xx.

Selection: `LLM_PROVIDER` env if set; else `anthropic` if `ANTHROPIC_API_KEY`; else `ollama`
if `GET {OLLAMA_BASE_URL}/api/tags` answers within 1 s at startup; else `none`. Exposed as
`GET /api/system/llm` → `{"provider", "model", "reachable"}` (the Inspector shows a badge).
`RiskAggregator` keeps delegating low-confidence cases to this service unchanged.

## 8. Frontend

### 8.1 Data-source layer

`dataSource.js` defines one interface (`stats.overview/series`, `alerts.list/setStatus`,
`transactions.list`, `graph.subgraph/shortestPath/flow/account`, `risk.evaluate`, `ai.explain`,
`pipeline.overview/communities/subgraph/marked/threshold/metrics/metricsCurve/run/runStatus/latestRun`,
`datasets.current/upload/template`, `system.llm`). `createLiveSource(baseUrl)` = axios.
`createSnapshotSource()` lazy-imports `src/data/snapshot/*.json`; unsupported calls reject with
`NotInSnapshotError` (rendered as an inline "needs a live backend → self-host" note);
`pipeline.metrics(cutoff)` interpolates the baked curve; `alerts.setStatus` updates local state
only and toasts "demo — not persisted".

`DataSourceProvider`: if `VITE_API_BASE_URL` is set, probe `GET /health` (2 s); success → `live`,
failure → `demo` with a banner "backend unreachable — showing snapshot" and a retry button.
Unset → `demo`. The Sidebar's `ModePill` shows **Live** (green dot) or **Demo · snapshot <date>**;
the alerts badge is `open_flags.total`.

### 8.2 Routing

A hash router replaces the `view` state (`#/overview`, `#/graph?account=…`, `#/alerts?id=…`,
`#/transactions?account=…`, `#/pipeline`, `#/upload`). `navigateTo(view, ctx)` keeps its
signature and writes the hash, so existing cross-links keep working and README links can deep-link.

### 8.3 Views

- **Overview (Dashboard)** — hero chart per selected currency (selector, default US Dollar), periods
  24H / 7D / All, "Now" label becomes the dataset end date; hover/brush interactions unchanged.
  Stat strip: Accounts scored · Communities · Cycles (in-cycle) · Open flags (no fake deltas —
  the "vs prior" line becomes a context caption). Feeds: `alerts.list({limit:6})` and
  `transactions.list({limit:6})`. Loading skeletons; empty/error states.
- **Alerts** — `alerts.list` with the severity filter → `min_level`, a type filter (AGGREGATE /
  COMMUNITY / CYCLE / LIVE_GNN), pagination; row shows type label, level, `last_detected_at`,
  explanation, primary account (+ "and N more"), score as confidence; expanded row shows the
  full explanation, signals, and the three actions wired to `setStatus` (escalated / reviewed /
  dismissed) with toasts; "Open in Graph".
- **Transactions** — History only (the fictional In-Flight tab, ACH/wire/card mocks and the
  live counter are removed). Currency filter, risk chips from tiers, status chip `flagged` /
  `settled` (new tone), account search → per-account query (live) or featured accounts (demo);
  "Open in Graph". A compact by-currency strip from `overview.currencies`.
- **Graph** — restyled to the light system (canvas `bg-base`, risk colours from `RISK_VAR`,
  selection in accent). Search + depth; **GraphTools** drawer: shortest path (two ids → path
  drawn on the canvas + hop count) and flow (two ids + window → volume/count summary). Demo
  mode offers "featured account" chips and the precomputed pairs. **Inspector** (light): score &
  tier, signals (GNN score, in-cycle, community, PageRank), aggregator verdict (existing
  `/risk/evaluate`), AI explanation with provider badge, the what-if "add a 3-hop cycle" button
  (kept, relabelled). The business-summary form is removed.
- **Pipeline** — header: latest run status + **Run pipeline** (live) / "precomputed run from
  <date>" (demo); progress bar polling `runStatus` every 1.5 s. Threshold panel: slider seeded
  from `threshold.default`, whole-graph precision/recall/FP/TP readout, and a P/R-vs-cutoff
  chart from `metricsCurve` (hidden when unlabelled). Lenses: GNN heat · PageRank size · Louvain
  colour · Marked vs dataset (labelled only). Overview graph (`GraphCanvas`, node-cap slider,
  hover highlight, labels off above 200 nodes). Communities table (sort risk/size → subgraph).
  Marked table (score, signals, rationale → Graph).
- **Upload** (live only; nav item hidden in demo) — drop zone, template download, optional
  patterns file, "I understand this replaces the current graph" checkbox, staged progress; on
  completion navigates to Pipeline.

## 9. Snapshot generator (`scripts/export_snapshot.py`)

Runs against the live stores (service functions, not HTTP). Flags: `--llm ollama|anthropic|none`
(default: the service's own selection), `--featured 30`, `--out Frontend/src/data/snapshot`.
Writes: `meta.json` (generated_at, dataset, llm provider/model), `overview.json`, `series.json`
(every currency × period), `alerts.json` (top 200 by score + per-type counts), `transactions.json`
(latest 200 + ≤ 50 per featured account), `featured.json` (top marked accounts by combined score,
diversified across typologies when labelled), `graph/<id>.json` (depth-2 subgraph each),
`accounts/<id>.json` (node + aggregator verdict + explanation), `paths.json` and `flows.json`
(5 connected pairs among featured accounts), `pipeline/overview_gnn.json`, `overview_pagerank.json`,
`communities.json`, `marked.json`, `threshold.json`, `metrics_curve.json`, `latest_run.json`.
Prints per-file sizes and **fails if the total exceeds 4 MB**. Idempotent; committed output.

## 10. Error handling & resilience

- Every view has loading / empty / error states; a failed request never blanks the page.
- Stats warm-up returns 503 with a clear detail; the client retries with backoff.
- Explanation service never raises to the client; provider failures degrade to `none`.
- Upload validates before touching the stores; any stage failure records `status:"failed"`
  with the stage and error (existing `pipeline_runs` semantics). The previous graph is gone
  once `reset` starts — the UI says so before confirm, and the README explains how to reload
  the IBM demo (`run_ingest --reset`).
- Long uploads are bounded by `export_timeout_seconds` and the existing sampler caps.

## 11. Testing

Backend (pytest, fake clients like `tests/test_viz_*`): `test_stats_service.py` (histogram →
series math, anchoring, baseline, currency labels, 503-while-warming), `test_alerts_api.py`
(filters, offset/total, status update, 404/422), `test_transactions_service.py` (tier max,
flagged join, per-account), `test_graph_service.py` (score coalescing, node fields, flow over
TRANSFER), `test_explanation_service.py` (evidence builder, provider selection, schema
validation, fallback on provider error), `test_dataset_runner.py` (validation, stage order,
live feature source, meta rewrite, truth reload), `test_snapshot_export.py` (shape + size
budget on a tiny fake graph), `test_pipeline_alias.py` (both prefixes serve, curve endpoint).
Live smoke against the running stack for each new endpoint.

Frontend: `npm run lint` + `npm run build`; a manual end-to-end pass on every view in **both**
modes with screenshots (which also become README images).

CI (`.github/workflows/ci.yml`): backend `pytest` (DB-dependent tests self-skip) and frontend
lint + build on push/PR.

## 12. Docs, release, deploy (WP5)

- **README** — rewritten around the reader: hero screenshot + live demo link; 60-second pitch;
  mermaid **architecture** and **data-flow** diagrams; "what you're looking at" per view with
  screenshots; results section with charts (`scripts/plot_results.py` → `docs/images/`:
  PR-AUC progression, recall by typology detectors vs GNN vs ensemble); 5-minute self-host
  quickstart (compose → fetch models → ingest → run API → run frontend, with the exact
  `POSTGRES_DSN`); "run it on your own data" (template + upload); explanation providers
  (Ollama default, Claude opt-in with cost note); the honest limitations; repo layout. The
  existing ML narrative moves to `Backend/ml/RESULTS.md` (already the detailed record) and is
  linked, not duplicated.
- `Frontend/README.md` replaced (dev, env, demo vs live, snapshot regeneration).
- `Backend/CLAUDE.md` status section refreshed (query API, frontend, enrichment no longer "not started").
- **Models**: `ml/runs/v10_L3{,_s1,_s7}` + `ml/cache/featureset_v4.npz` zipped as a GitHub Release
  asset (`models-v1`); `scripts/fetch_models.py` downloads and unpacks them. Creating the release
  is a manual step if `gh` is not authenticated.
- **Vercel**: root directory `Frontend`, framework Vite, no env vars (→ demo). `Frontend/vercel.json`
  pins the framework; hash routing means no rewrites are needed.
- **Follow-up (separate PR, flagged only)**: `git rm --cached` the six tracked 94 MB CSV chunks
  (they are ignored by rule but were force-added); optionally move them to a release asset.

## 13. Build order

WP0 prerequisites → WP1 backend read API + graph fixes → WP2 frontend (data-source layer,
routing, restyle, wire views, Pipeline view, Graph tools) → WP6 explanation service (+ Inspector
wiring) → WP4 snapshot generator + demo mode → WP3 upload → WP5 docs/CI/deploy → PR.

## 14. Risks

| Risk | Mitigation |
|---|---|
| `FeatureBuilder` on a very large upload takes many minutes | Documented; job is async with stage progress; typical uploads are small |
| Small local models produce off-schema JSON | Schema-enforced generation + Pydantic validation + rule-based fallback |
| Snapshot bloat | Hard 4 MB budget enforced by the generator; lazy per-file imports |
| Relationship property index unsupported | Neo4j 5 in compose supports it; `init_constraints` logs and continues, and the endpoint still works (slower) |
| Python 3.9 on the dev machine | All new code 3.9-safe; CI runs 3.9 |
