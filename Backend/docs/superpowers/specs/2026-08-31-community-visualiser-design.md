# Community & Pipeline Visualiser — Design Spec

- **Date:** 2026-08-31
- **Status:** Approved (design) — pending implementation plan
- **Author:** KavEn06 (with Claude)
- **Branch:** `test-setup` (PR #23 lineage) — see Open Questions re: target branch

## 1. Summary

A **standalone, backend-served viewer** that runs FlowGraph's real detection
pipeline over the ingested IBM AML graph (513,987 accounts in Neo4j) and lets a
user explore each stage's output on the actual graph. Served by the existing
FastAPI app at `/viz`, entirely separate from the mock React frontend.

Five tabs — **Cycle · PageRank · Louvain · GNN · Marked** — re-style the *same*
loaded subgraph. Navigation is by community browse **and** account + hop-count
search. A **Run pipeline** button launches the pipeline as an async job
(inference only — no re-ingest, no retrain) and shows progress.

## 2. Context — what already exists (the reframe)

This feature is a **surfacing + orchestration layer**, not new algorithms. Every
computation already exists in the backend:

| Stage | Existing module | Notes |
|---|---|---|
| PageRank | `Neo4jClient.recompute_pagerank_full` (called in `ml/datasets/run_ingest.py:185`) | writes `pagerank_score` to Neo4j nodes |
| Louvain communities | `fraud/community_detector.py` (`CommunityDetector`, `partition_graph`, `score_community`) | writes `community_id` node props + community scores |
| Cycle detection | `fraud/cycle_detector.py` (`CycleDetector.detect`, `score_cycle`, `cycle_fingerprint`) | upserts into Postgres `risk_flags`, idempotent on fingerprint |
| GNN | `ml/predict.py` (`load_run`, `risk_level`), `ml/ensemble.py` (`ensemble_scores`), `ml/features.py` (`FeatureBuilder` → `FeatureSet.node_ids`) | champion run `ml/runs/v10_L3`; scores align to `FeatureSet.node_ids` |
| Ground truth / eval | `ml/evaluate.py` (`load_ground_truth`, `compare_against_detector`) | not required for the Marked tab (see §6) but available |
| Subgraph read | `app/services/graph_service.py` (`get_subgraph`) | APOC + non-APOC fallback; emits Cytoscape `{nodes, edges}` with edge `weight = clamp(total_amount/100000, 1, 10)` |

The **only genuinely new persistence** is the per-account GNN score, the
aggregated "mark", and a job-status table.

## 3. Resolved requirements

| Decision | Answer |
|---|---|
| Form | Standalone viewer served by FastAPI at `/viz`, separate from the mock frontend |
| Tabs | Cycle · PageRank · Louvain · GNN · Marked — each draws **results on the real graph** (no execution animation) |
| Compute | **Hybrid**: persisted per-account results + a **Run pipeline** button (async job + progress) |
| Run semantics | Re-run algorithms + GNN **inference** over the already-ingested graph. **No** re-ingest of the 5M rows, **no** GNN retrain |
| Marked view | **Aggregated system flags** — union/consensus of GNN risk + in-a-cycle + high-risk community, showing which signals fired + a combined score |
| Navigation | Community browse **and** account + hop-count (1–4) search; clicking a node re-centers on its neighborhood |
| Edge rendering | Thickness ∝ `FLOWS_TO.total_amount`; **directed arrowheads** (source→target) |
| Rendering lib | **Cytoscape.js**, vendored (no CDN), with `cose-bilkent` layout |

## 4. Non-goals (YAGNI guardrails)

- No step-by-step algorithm animation / playback engine.
- No GNN retraining; no dataset re-ingestion.
- No authentication / multi-user concerns.
- No websockets — progress is **polled**.
- One fixed dataset (HI-Small) at a time; no dataset switching UI.
- Not wired into the live streaming consumer path (this is batch/offline surfacing).

## 5. Architecture

New package `app/viz/`, mounted into the existing FastAPI app
(`app/api/main.py` includes the router; keeps one server/process).

```
app/viz/
  __init__.py
  runner.py     # PipelineRunner — orchestrates the 5 stages with progress callbacks
  store.py      # read/write per-account results + job status (Neo4j + Postgres)
  aggregate.py  # marked-account aggregation rule (which signals fired + combined score)
  router.py     # FastAPI routes under prefix /viz
  schemas.py    # pydantic response models (communities, subgraph, marked, run status)
  static/
    index.html
    app.js
    styles.css
    vendor/cytoscape.min.js
    vendor/cytoscape-cose-bilkent.js
```

**Reuse, don't reimplement:** `runner.py` calls `CycleDetector`,
`CommunityDetector`, `recompute_pagerank_full`, and the `ml/` inference path.
`/viz/subgraph` reuses/extends `graph_service.get_subgraph`'s Cypher (APOC +
fallback) rather than writing new traversal.

## 6. Data & persistence model

**Neo4j `Account` node properties**
- `pagerank_score: float` — exists (written by PageRank stage)
- `community_id: str/int` — exists (written by Louvain stage; fingerprint-derived)
- `gnn_risk_score: float` — **new** (0–1, from GNN inference)
- `gnn_risk_tier: str` — **new** (low/medium/high/critical via `ml.predict.risk_level`)
- `in_cycle: bool` — **new** (true if the account is a member of any detected cycle)

**Postgres**
- `risk_flags` — exists. Cycle flags already land here via `CycleDetector`. The
  aggregated "mark" is written here too (flag type `AGGREGATE`) so the existing
  risk-flag store remains the source of truth, OR a dedicated `marked_accounts`
  view/table derived from it. *(Implementation plan picks one; default: extend
  `risk_flags` with the aggregate type, consistent with existing conventions.)*
- `pipeline_runs` — **new**:
  `id (uuid) · status (queued|running|completed|failed) · stage (str) ·
  progress (0.0–1.0) · counts (jsonb: cycles, communities, marked, …) ·
  started_at · finished_at · error (nullable text)`.

**Aggregation rule (`aggregate.py`)** — an account is **marked** if *any* of:
- `gnn_risk_score ≥ τ_gnn` (default `τ_gnn` = the champion run's stored
  validation-selected threshold from `result.json`; fallback 0.5), OR
- `in_cycle == true`, OR
- `community_risk_tier ∈ {high, critical}` (from `score_community`).

`combined_score` = weighted blend of available signals (default weights
`gnn 0.6 / cycle 0.25 / community 0.15`, renormalized over present signals),
plus a `signals: {gnn, cycle, community}` boolean map so the UI shows *which*
fired. All thresholds/weights live in `app/core/config.py` (env-overridable).

> **Convention note:** risk scores are always emitted with an explanation
> (existing repo rule). The Marked payload includes a short generated rationale
> string per account (which signals fired + values) — no bare numeric score.

## 7. Pipeline runner (`PipelineRunner`)

Ordered stages, each emitting a progress callback that updates `pipeline_runs`:

1. **PageRank** → `recompute_pagerank_full` (writes `pagerank_score`).
2. **Louvain** → `CommunityDetector.partition_graph` / detect (writes
   `community_id` + community scores).
3. **Cycle** → `CycleDetector.detect` (upserts cycle `risk_flags`); derive
   `in_cycle` per account from the returned cycle node lists and persist.
4. **GNN inference** → `FeatureBuilder` assembles the 47-col `FeatureSet` from
   live stores (or loads the cached `.npz` if the graph is unchanged); load
   champion `ml/runs/v10_L3` (single model, or the 3-seed ensemble if the
   `_s1/_s7` runs are present) via `load_run`/`ensemble_scores`; apply the saved
   scaler; write `gnn_risk_score` + `gnn_risk_tier` per `FeatureSet.node_ids`.
5. **Aggregate** → `aggregate.py` computes marks + combined score, writes the
   aggregate `risk_flags`, tallies `counts`.

Execution: FastAPI **background task** (async) kicked by `POST /viz/run`. Single
active run at a time — concurrent starts are **rejected with HTTP 409** (matches
§8), never queued. Status is written to `pipeline_runs` and polled by the UI.

**Failure handling:** each stage wrapped in try/except; on error set
`status=failed`, record the failing `stage` + message; earlier stages' persisted
results remain valid. Re-runs are idempotent (Neo4j `SET`, `risk_flags` upsert
on fingerprint).

## 8. API — routes under `/viz`

All JSON except `GET /viz/`. Pydantic models in `schemas.py`.

| Method · Path | Params | Response |
|---|---|---|
| `GET /viz/` | — | the viewer HTML page (static) |
| `GET /viz/communities` | `sort=risk\|size` (default risk), `limit` (≤200), `offset` | `[{community_id, size, risk_score, risk_tier, flagged_count}]` |
| `GET /viz/subgraph` | **one of** `community_id=…` **or** `account_id=…&hops=1..4`; `limit` (nodes, default 150, ≤500) | Cytoscape `{nodes:[{data:{id,label,node_type,pagerank_score,community_id,gnn_risk_score,gnn_risk_tier,in_cycle,marked,signals}}], edges:[{data:{id,source,target,total_amount,tx_count,weight}}], truncated:{shown,total}}` |
| `GET /viz/marked` | `sort=score` (default), `signal=gnn\|cycle\|community` (filter), `limit`, `offset` | `[{account_id, combined_score, signals:{gnn,cycle,community}, gnn_risk_score, community_id, in_cycle, rationale}]` |
| `GET /viz/account/{id}` | — | inspector detail (all node props + incident edge summary) |
| `POST /viz/run` | — | `{run_id}` (409 if a run is active) |
| `GET /viz/run/{run_id}` | — | `{status, stage, progress, counts, started_at, finished_at, error}` |
| `GET /viz/run/latest` | — | latest run status (for UI load) |

CORS/mounting: router included under the existing app; the static page is
same-origin so no extra CORS entry needed.

## 9. Viewer behavior (`static/`)

Single self-contained page. Vendored Cytoscape.js + cose-bilkent layout. State:
currently-loaded subgraph (from `/viz/subgraph`) + active tab.

**Tabs** re-style the *same* elements (no refetch on tab switch):
- **Cycle** — nodes with `in_cycle` highlighted (red); their ring edges emphasized.
- **PageRank** — node size ∝ `pagerank_score` (normalized within the subgraph).
- **Louvain** — node fill = categorical color keyed by `community_id`; legend.
- **GNN** — node fill = sequential heat by `gnn_risk_score`; tier legend.
- **Marked** — dim un-marked nodes; marked nodes get a border whose segments/icon
  indicate which signals fired; click → breakdown panel (per-signal values + rationale).

**All tabs (constant styling):**
- Edge width ∝ `total_amount` (reuse backend `weight`, clamp for readability).
- **Directed arrowheads** source→target.
- Click a node → fetch its `hops`-hop subgraph and re-center; push to a small
  breadcrumb/back stack.
- Inspector panel: account id, node_type, all stage results, incident-edge totals.

**Search / navigation bar:**
- Community picker (populated from `/viz/communities`, sorted by risk) → loads that
  community's subgraph.
- Account-ID input + hop selector (1–4) → loads that neighborhood.

**Run pipeline button:** `POST /viz/run` → disable + show progress bar polling
`GET /viz/run/{id}` every ~1.5s → on `completed`, toast + refresh current view;
on `failed`, show the failing stage + message.

**Empty/edge states:** unknown account → "no such account"; empty community →
message; subgraph over the node cap → "showing N of M — refine search"; GNN not
yet run → GNN/Marked tabs show "run the pipeline to populate scores."

## 10. Error handling (summary)

- Run job: per-stage isolation, `failed` status carries stage + message; partial
  results preserved; idempotent re-run.
- Missing GNN artifacts (`ml/runs/v10_L3` or feature inputs absent): stage 4 fails
  with an actionable message; API surfaces it; UI GNN/Marked tabs degrade gracefully.
- Oversized subgraphs: hard node cap (`limit`), `truncated` metadata returned.
- Standard 404s for unknown account/community/run id.

## 11. Testing strategy

Mirror existing `tests/` patterns (pytest + pytest-asyncio).

- **Unit**
  - `PipelineRunner` stage orchestration with the four underlying services mocked
    (order, progress callbacks, failure short-circuit + status).
  - `aggregate.py`: signal-firing truth table + combined-score math + rationale.
  - Subgraph assembly: Cytoscape shape, edge `weight`/arrow direction, node cap +
    `truncated`.
  - Marked ranking + `signal` filter.
- **API** (FastAPI `TestClient`, store mocked): each endpoint's shape, params,
  and error paths (404s, 409 on concurrent run).
- **Integration (optional, gated like existing DB tests):** run the pipeline
  end-to-end against the 13-account `acc_*` seed graph (from
  `scripts/seed_mock_data.py`); assert the known cycle
  (`acc_cycle_alpha_01→beta→gamma`) shows `in_cycle`, communities populate, GNN
  scores exist, and the fan-out hub surfaces in Marked.

## 12. Assumptions & open questions

1. **Target branch.** Design work sits on `test-setup` (PR #23). Confirm whether
   this feature lands on `test-setup`, `gnn-prep`, or a fresh feature branch off
   `main`. *(Recommendation: a fresh `feature/community-visualiser` branch off the
   branch that carries both the `app/` API and the `ml/` layer — likely
   `gnn-prep` or a merge of it — since it needs both.)*
2. **GNN run availability.** `ml/runs/v10_L3` is gitignored/regenerable. The
   pipeline's GNN stage requires a trained run present locally; if absent, it must
   be reproduced (per CLAUDE.md's "Reproducing the champion") or the stage fails
   cleanly. Ensemble (`_s1/_s7`) is optional; single model is the default.
3. **Feature freshness.** GNN inference can use the cached `.npz` when the graph
   is unchanged, else rebuild via `FeatureBuilder`. Plan should pick the trigger.
4. **Threshold/weights** in the aggregation rule are defaults; exposed in config.

## 13. Rough implementation phases (input to writing-plans)

1. Persistence: `pipeline_runs` migration + `store.py` (read/write node props +
   job status) + new node props.
2. `aggregate.py` + unit tests (pure logic, no I/O) — TDD-friendly first.
3. `PipelineRunner` orchestrating existing services + progress + failure paths.
4. `router.py` + `schemas.py` (read endpoints first, then `run`), API tests.
5. `static/` viewer: subgraph render (edges/arrows/thickness) → tabs → search →
   run button.
6. Integration test on the seed graph; docs update.
