# Community Visualiser — Backend Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend for the community/pipeline visualiser — persist per-account pipeline results, orchestrate the existing algos + GNN inference behind a Run button, and serve everything over a new `/viz` FastAPI API.

**Architecture:** A new `app/viz/` package mounted into the existing FastAPI app. A `PipelineRunner` drives the *existing* modules in order (PageRank → Louvain → cycle → GNN inference → aggregate) as an async background task, writing results to Neo4j node props + Postgres. Read endpoints serve community lists, subgraphs (Cytoscape shape), the marked-accounts list, and job status. This plan produces working, curl-testable software on its own; the static Cytoscape viewer (Plan 2) consumes these endpoints.

**Tech Stack:** Python 3.9, FastAPI 0.115, pydantic 2 / pydantic-settings, asyncpg (Postgres), neo4j async driver, pytest + pytest-asyncio. Reuses `db.neo4j.Neo4jClient`, `db.postgres.PostgresClient`, `fraud.cycle_detector.CycleDetector`, `fraud.community_detector.CommunityDetector`, `ml.ensemble.ensemble_scores`, `ml.train.load_feature_cache`, `ml.predict.risk_level`/`explain`.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-31-community-visualiser-design.md` — this plan implements its §5–§11.
- **Python 3.9** (system interpreter here). No 3.10+ syntax (`X | Y` unions only where `from __future__ import annotations` is present; prefer `Optional[...]`).
- **Cypher uses named parameters, never string interpolation** (repo convention). The one allowed exception (variable-length bound) is clamped to `1..4` first, mirroring `app/services/graph_service.py`.
- **Risk scores always ship a written explanation** — never persist a bare numeric score; `upsert_risk_flag` enforces non-empty `explanation`.
- **Fit scalers on train only / inference-only** — the GNN stage applies the *saved* scaler via `ensemble_scores`; it never re-fits, re-trains, or re-ingests.
- **Two Neo4j clients:** the runner uses the full `db.neo4j.Neo4jClient` (has `recompute_pagerank_full`, `write_community_assignments`, `find_cycles`); read endpoints reuse the app's `app.db.neo4j.neo4j_client` session (like `graph_service`). Do not confuse them.
- **All time values UTC**; timestamps stored as unix seconds where the existing schema does.
- Feature column order is a model contract — the GNN stage must score against the same cache the champion trained on (`feature_names` equality is asserted by `ensemble_scores`).

---

## File Structure

**Create:**
- `migrations/003_create_pipeline_runs_table.sql` — job-status table.
- `app/viz/__init__.py`
- `app/viz/schemas.py` — pydantic response models.
- `app/viz/aggregate.py` — pure marked-account aggregation logic (no I/O).
- `app/viz/store.py` — read helpers (community list, subgraph, marked, run) over the app Neo4j session + shared Postgres.
- `app/viz/runner.py` — `PipelineRunner` (orchestration + progress).
- `app/viz/deps.py` — shared client accessors (root `Neo4jClient` + `PostgresClient`) for the runner and reads.
- `app/viz/router.py` — `/viz` routes.
- `tests/test_viz_aggregate.py`, `tests/test_viz_store.py`, `tests/test_viz_runner.py`, `tests/test_viz_api.py`, `tests/test_pipeline_runs.py`.

**Modify:**
- `db/neo4j.py` — add `write_gnn_scores`, `write_cycle_membership` (batched `MATCH…SET`, mirroring `write_community_assignments`).
- `db/postgres.py` — add `create_pipeline_run`, `update_pipeline_run`, `get_pipeline_run`, `get_latest_pipeline_run`, `get_active_pipeline_run`.
- `app/core/config.py` — add viz settings (GNN paths, mark thresholds/weights).
- `app/api/main.py` — mount the viz router; init shared clients in lifespan.

---

## Task 1: Marked-account aggregation logic (`aggregate.py`)

Pure functions, no I/O — the cleanest thing to TDD first. Given a per-account record of the three signals, decide *marked* and compute a combined score + rationale.

**Files:**
- Create: `app/viz/aggregate.py`
- Test: `tests/test_viz_aggregate.py`

**Interfaces:**
- Produces:
  - `@dataclass MarkWeights(gnn: float = 0.6, cycle: float = 0.25, community: float = 0.15)`
  - `@dataclass MarkThresholds(gnn: float = 0.5, community_tiers: frozenset = frozenset({"high", "critical"}))`
  - `aggregate_account(account_id: str, gnn_score: Optional[float], in_cycle: bool, community_tier: Optional[str], weights: MarkWeights, thresholds: MarkThresholds) -> Optional[dict]` — returns `None` if not marked, else `{account_id, combined_score: float, signals: {"gnn": bool, "cycle": bool, "community": bool}, gnn_score, in_cycle, community_tier, rationale: str}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_viz_aggregate.py
from app.viz.aggregate import aggregate_account, MarkWeights, MarkThresholds

W = MarkWeights()
T = MarkThresholds()

def test_unmarked_returns_none():
    assert aggregate_account("a", gnn_score=0.1, in_cycle=False,
                             community_tier="low", weights=W, thresholds=T) is None

def test_gnn_signal_marks_and_scores():
    out = aggregate_account("a", gnn_score=0.92, in_cycle=False,
                            community_tier="low", weights=W, thresholds=T)
    assert out is not None
    assert out["signals"] == {"gnn": True, "cycle": False, "community": False}
    # only the gnn signal is present, so combined == its own score
    assert out["combined_score"] == 0.92
    assert "gnn" in out["rationale"].lower()

def test_cycle_signal_marks_with_no_gnn():
    out = aggregate_account("a", gnn_score=None, in_cycle=True,
                            community_tier=None, weights=W, thresholds=T)
    assert out["signals"] == {"gnn": False, "cycle": True, "community": False}
    assert out["combined_score"] == 1.0  # cycle signal contributes 1.0

def test_multiple_signals_blend_and_renormalize():
    out = aggregate_account("a", gnn_score=0.8, in_cycle=True,
                            community_tier="critical", weights=W, thresholds=T)
    assert out["signals"] == {"gnn": True, "cycle": True, "community": True}
    # weighted mean over present signals: (.6*.8 + .25*1 + .15*1) / (.6+.25+.15)
    assert abs(out["combined_score"] - (0.6*0.8 + 0.25*1 + 0.15*1)) < 1e-9

def test_community_tier_below_threshold_is_not_a_signal():
    out = aggregate_account("a", gnn_score=0.92, in_cycle=False,
                            community_tier="medium", weights=W, thresholds=T)
    assert out["signals"]["community"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_viz_aggregate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.viz.aggregate'`.

- [ ] **Step 3: Implement `aggregate.py`**

```python
# app/viz/aggregate.py
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass(frozen=True)
class MarkWeights:
    gnn: float = 0.6
    cycle: float = 0.25
    community: float = 0.15


@dataclass(frozen=True)
class MarkThresholds:
    gnn: float = 0.5
    community_tiers: frozenset = field(default_factory=lambda: frozenset({"high", "critical"}))


def aggregate_account(
    account_id: str,
    gnn_score: Optional[float],
    in_cycle: bool,
    community_tier: Optional[str],
    weights: MarkWeights,
    thresholds: MarkThresholds,
) -> Optional[Dict]:
    gnn_fired = gnn_score is not None and gnn_score >= thresholds.gnn
    cycle_fired = bool(in_cycle)
    community_fired = community_tier in thresholds.community_tiers

    if not (gnn_fired or cycle_fired or community_fired):
        return None

    # Each present signal contributes a [0,1] value; blend by weight, renormalize
    # over only the signals that fired so a lone signal scores as itself.
    parts = []  # (weight, value)
    if gnn_fired:
        parts.append((weights.gnn, float(gnn_score)))
    if cycle_fired:
        parts.append((weights.cycle, 1.0))
    if community_fired:
        parts.append((weights.community, 1.0))
    wsum = sum(w for w, _ in parts)
    combined = sum(w * v for w, v in parts) / wsum

    fired = []
    if gnn_fired:
        fired.append(f"GNN risk {gnn_score:.2f}")
    if cycle_fired:
        fired.append("member of a detected cycle")
    if community_fired:
        fired.append(f"in a {community_tier}-risk community")
    rationale = f"Marked ({', '.join(fired)}); combined score {combined:.2f}."

    return {
        "account_id": account_id,
        "combined_score": combined,
        "signals": {"gnn": gnn_fired, "cycle": cycle_fired, "community": community_fired},
        "gnn_score": gnn_score,
        "in_cycle": cycle_fired,
        "community_tier": community_tier,
        "rationale": rationale,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_viz_aggregate.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/viz/__init__.py app/viz/aggregate.py tests/test_viz_aggregate.py
git commit -m "feat(viz): marked-account aggregation logic"
```
(Create an empty `app/viz/__init__.py` in this step if it does not exist.)

---

## Task 2: `pipeline_runs` persistence (migration + Postgres methods)

**Files:**
- Create: `migrations/003_create_pipeline_runs_table.sql`
- Modify: `db/postgres.py` (add methods after `get_outbox_stats`)
- Test: `tests/test_pipeline_runs.py`

**Interfaces:**
- Produces (on `PostgresClient`):
  - `async create_pipeline_run() -> str` (returns a new uuid `run_id`, status `queued`)
  - `async update_pipeline_run(run_id: str, *, status: Optional[str] = None, stage: Optional[str] = None, progress: Optional[float] = None, counts: Optional[dict] = None, error: Optional[str] = None, finished: bool = False) -> None`
  - `async get_pipeline_run(run_id: str) -> Optional[dict]`
  - `async get_latest_pipeline_run() -> Optional[dict]`
  - `async get_active_pipeline_run() -> Optional[dict]` (status in `queued`/`running`, else None)

- [ ] **Step 1: Write the migration**

```sql
-- migrations/003_create_pipeline_runs_table.sql
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','completed','failed')),
    stage        TEXT,
    progress     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    counts       JSONB NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs (status);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs (started_at DESC);
```

- [ ] **Step 2: Write the failing test**

Reuse the Postgres fixture pattern from `tests/test_database_clients.py` (testcontainers). Mark the module so it is skipped when Docker is unavailable, matching the existing DB tests.

```python
# tests/test_pipeline_runs.py
import pytest

pytestmark = pytest.mark.asyncio  # + the repo's DB-integration marker if present

async def test_create_and_fetch_run(pg_client):          # pg_client: initialized PostgresClient
    run_id = await pg_client.create_pipeline_run()
    row = await pg_client.get_pipeline_run(run_id)
    assert row["id"] == run_id and row["status"] == "queued"
    assert (await pg_client.get_active_pipeline_run())["id"] == run_id

async def test_update_progress_and_finish(pg_client):
    run_id = await pg_client.create_pipeline_run()
    await pg_client.update_pipeline_run(run_id, status="running", stage="pagerank", progress=0.2)
    row = await pg_client.get_pipeline_run(run_id)
    assert row["stage"] == "pagerank" and row["progress"] == 0.2
    await pg_client.update_pipeline_run(run_id, status="completed", progress=1.0,
                                        counts={"marked": 3}, finished=True)
    row = await pg_client.get_pipeline_run(run_id)
    assert row["status"] == "completed" and row["counts"]["marked"] == 3
    assert row["finished_at"] is not None
    assert await pg_client.get_active_pipeline_run() is None
```

Run: `python3 -m pytest tests/test_pipeline_runs.py -v`
Expected: FAIL — methods do not exist (or fixture missing; add a `pg_client` fixture mirroring `tests/test_database_clients.py`, and run migration `003` in it).

- [ ] **Step 3: Implement the methods on `PostgresClient`**

```python
# db/postgres.py  (add inside class PostgresClient)
import json
import uuid

async def create_pipeline_run(self) -> str:
    run_id = str(uuid.uuid4())
    async with self._get_connection() as conn:
        await conn.execute(
            "INSERT INTO pipeline_runs (id, status) VALUES ($1, 'queued')", run_id
        )
    return run_id

async def update_pipeline_run(self, run_id, *, status=None, stage=None,
                              progress=None, counts=None, error=None, finished=False):
    sets, args = [], []
    def add(col, val):
        args.append(val)
        sets.append(f"{col} = ${len(args)}")
    if status is not None: add("status", status)
    if stage is not None: add("stage", stage)
    if progress is not None: add("progress", progress)
    if counts is not None: add("counts", json.dumps(counts))
    if error is not None: add("error", error)
    if finished: sets.append("finished_at = now()")
    if not sets:
        return
    args.append(run_id)
    async with self._get_connection() as conn:
        await conn.execute(
            f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id = ${len(args)}", *args
        )

async def _row_to_run(self, row):
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("counts"), str):
        d["counts"] = json.loads(d["counts"])
    d["id"] = str(d["id"])
    return d

async def get_pipeline_run(self, run_id):
    async with self._get_connection() as conn:
        row = await conn.fetchrow("SELECT * FROM pipeline_runs WHERE id = $1", run_id)
    return await self._row_to_run(row)

async def get_latest_pipeline_run(self):
    async with self._get_connection() as conn:
        row = await conn.fetchrow("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 1")
    return await self._row_to_run(row)

async def get_active_pipeline_run(self):
    async with self._get_connection() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM pipeline_runs WHERE status IN ('queued','running') "
            "ORDER BY started_at DESC LIMIT 1"
        )
    return await self._row_to_run(row)
```
> Note: asyncpg returns `jsonb` as a str; `_row_to_run` decodes it. Confirm `_get_connection()` is an async context manager (it is — `db/postgres.py:65`).

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_pipeline_runs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/003_create_pipeline_runs_table.sql db/postgres.py tests/test_pipeline_runs.py
git commit -m "feat(viz): pipeline_runs job-status table + PostgresClient methods"
```

---

## Task 3: Neo4j result writers (`write_gnn_scores`, `write_cycle_membership`)

Batched `MATCH…SET`, modeled exactly on `write_community_assignments` (`db/neo4j.py:770`).

**Files:**
- Modify: `db/neo4j.py`
- Test: `tests/test_viz_store.py::test_write_*` (integration, gated like other Neo4j tests)

**Interfaces:**
- Produces (on `Neo4jClient`):
  - `async write_gnn_scores(scores: Dict[str, float], tier_of: Callable[[float], str], batch_size: int = 10_000) -> int` — sets `gnn_risk_score` + `gnn_risk_tier` on matching accounts; returns count written.
  - `async write_cycle_membership(account_ids: Iterable[str], batch_size: int = 10_000) -> int` — sets `in_cycle = true` on the given accounts and `in_cycle = false` on all others (a full refresh so stale membership clears); returns count set true.

- [ ] **Step 1: Write the failing test** (integration; uses a Neo4j fixture that seeds a few `Account` nodes)

```python
# tests/test_viz_store.py
import pytest
from ml.predict import risk_level

pytestmark = pytest.mark.asyncio

async def test_write_gnn_scores(neo4j_client_seeded):     # fixture: 3 Account nodes a,b,c
    n = await neo4j_client_seeded.write_gnn_scores({"a": 0.95, "b": 0.1}, tier_of=risk_level)
    assert n == 2
    # verify via a read
    async with neo4j_client_seeded.driver.session() as s:
        rec = await (await s.run("MATCH (x:Account {id:'a'}) RETURN x.gnn_risk_score AS s, x.gnn_risk_tier AS t")).single()
    assert rec["s"] == 0.95 and rec["t"] == "critical"

async def test_write_cycle_membership_refreshes(neo4j_client_seeded):
    await neo4j_client_seeded.write_cycle_membership(["a", "b"])
    await neo4j_client_seeded.write_cycle_membership(["a"])   # b should flip back to false
    async with neo4j_client_seeded.driver.session() as s:
        rows = {r["id"]: r["c"] async for r in await s.run(
            "MATCH (x:Account) RETURN x.id AS id, x.in_cycle AS c")}
    assert rows["a"] is True and rows["b"] is False
```

Run: `python3 -m pytest tests/test_viz_store.py -k write -v`
Expected: FAIL — methods undefined.

- [ ] **Step 2: Implement the writers on `Neo4jClient`**

```python
# db/neo4j.py  (add inside class Neo4jClient; driver/session pattern as elsewhere in this file)
from typing import Callable, Iterable

async def write_gnn_scores(self, scores: Dict[str, float],
                           tier_of: Callable[[float], str], batch_size: int = 10_000) -> int:
    rows = [{"id": aid, "score": float(sc), "tier": tier_of(float(sc))}
            for aid, sc in scores.items()]
    written = 0
    async with self.driver.session() as session:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            res = await session.run(
                "UNWIND $rows AS r "
                "MATCH (a:Account {id: r.id}) "
                "SET a.gnn_risk_score = r.score, a.gnn_risk_tier = r.tier "
                "RETURN count(a) AS n", rows=chunk)
            written += (await res.single())["n"]
    return written

async def write_cycle_membership(self, account_ids: Iterable[str],
                                 batch_size: int = 10_000) -> int:
    ids = list(dict.fromkeys(account_ids))
    async with self.driver.session() as session:
        # clear everything, then set the current set true (full refresh)
        await session.run("MATCH (a:Account) WHERE a.in_cycle = true SET a.in_cycle = false")
        written = 0
        for i in range(0, len(ids), batch_size):
            chunk = ids[i:i + batch_size]
            res = await session.run(
                "UNWIND $ids AS aid MATCH (a:Account {id: aid}) "
                "SET a.in_cycle = true RETURN count(a) AS n", ids=chunk)
            written += (await res.single())["n"]
    return written
```
> Confirm the class exposes `self.driver` (it is created in `initialize()`); if it uses a session factory instead, mirror the exact pattern used by `write_community_assignments`.

- [ ] **Step 3: Run tests** — `python3 -m pytest tests/test_viz_store.py -k write -v` → PASS.

- [ ] **Step 4: Commit**

```bash
git add db/neo4j.py tests/test_viz_store.py
git commit -m "feat(viz): Neo4j writers for gnn_risk_score + in_cycle"
```

---

## Task 4: Read helpers (`store.py`) — communities, subgraph, marked

Read-only Cypher over the **app** session (`app.db.neo4j.neo4j_client.driver`), plus Postgres reads for marked/run. The subgraph shaper is unit-testable against a fake record; the Cypher queries get an integration test.

**Files:**
- Create: `app/viz/store.py`
- Test: `tests/test_viz_store.py` (add cases)

**Interfaces:**
- Consumes: `app.db.neo4j.neo4j_client` (session), `db.postgres.PostgresClient` (via `deps`), `aggregate.aggregate_account`.
- Produces:
  - `async list_communities(session, sort: str = "risk", limit: int = 100, offset: int = 0) -> list[dict]` → `[{community_id, size, risk_score, risk_tier, flagged_count}]`
  - `async load_subgraph(session, *, community_id=None, account_id=None, hops=2, limit=150) -> dict` → `{"nodes": [...], "edges": [...], "truncated": {"shown": int, "total": int}}` in Cytoscape shape (node data carries `pagerank_score, community_id, gnn_risk_score, gnn_risk_tier, in_cycle, marked, signals`; edge data carries `total_amount, tx_count, weight`).
  - `def shape_elements(nodes: list, rels: list) -> dict` — pure Cytoscape assembly (edge `weight = max(1.0, min(10.0, total_amount/100000.0))`, arrows implied by source→target), reused by `load_subgraph`; unit-tested directly.
  - `async list_marked(pg, sort="score", signal=None, limit=100, offset=0) -> list[dict]` — reads `risk_flags` of type `AGGREGATE` and returns rows with `signals` from `details`.

- [ ] **Step 1: Unit-test `shape_elements` (pure, no DB)**

```python
# tests/test_viz_store.py  (add)
from app.viz.store import shape_elements

def test_shape_elements_edge_weight_and_direction():
    nodes = [{"id": "a", "risk_score": 0.9, "pagerank_score": 0.04},
             {"id": "b", "risk_score": 0.1}]
    rels = [{"source": "a", "target": "b", "total_amount": 250000.0, "tx_count": 8}]
    out = shape_elements(nodes, rels)
    e = out["edges"][0]["data"]
    assert e["source"] == "a" and e["target"] == "b"      # direction preserved → arrowhead
    assert e["weight"] == min(10.0, 250000.0/100000.0)     # 2.5, thickness ∝ amount
    assert {n["data"]["id"] for n in out["nodes"]} == {"a", "b"}
```

Run: `python3 -m pytest tests/test_viz_store.py::test_shape_elements_edge_weight_and_direction -v`
Expected: FAIL — `shape_elements` undefined.

- [ ] **Step 2: Implement `store.py`**

```python
# app/viz/store.py
from typing import Optional, List, Dict, Any

_EDGE_W = lambda amt: max(1.0, min(10.0, float(amt or 0.0) / 100000.0))


def shape_elements(nodes: List[dict], rels: List[dict]) -> Dict[str, Any]:
    seen_n, out_n = set(), []
    for n in nodes:
        nid = n["id"]
        if nid in seen_n:
            continue
        seen_n.add(nid)
        out_n.append({"data": {
            "id": nid,
            "label": n.get("label", nid[:8]),
            "node_type": n.get("node_type", "account"),
            "pagerank_score": n.get("pagerank_score", 0.0),
            "community_id": n.get("community_id"),
            "gnn_risk_score": n.get("gnn_risk_score"),
            "gnn_risk_tier": n.get("gnn_risk_tier"),
            "in_cycle": bool(n.get("in_cycle", False)),
            "marked": bool(n.get("marked", False)),
            "signals": n.get("signals"),
        }})
    seen_e, out_e = set(), []
    for r in rels:
        rid = f"{r['source']}->{r['target']}"
        if rid in seen_e:
            continue
        seen_e.add(rid)
        out_e.append({"data": {
            "id": rid, "source": r["source"], "target": r["target"],
            "total_amount": float(r.get("total_amount", 0.0)),
            "tx_count": r.get("tx_count", 1),
            "weight": _EDGE_W(r.get("total_amount")),
        }})
    return {"nodes": out_n, "edges": out_e, "truncated": {"shown": len(out_n), "total": len(out_n)}}


async def list_communities(session, sort: str = "risk", limit: int = 100, offset: int = 0):
    order = "risk_score DESC" if sort == "risk" else "size DESC"
    q = (
        "MATCH (a:Account) WHERE a.community_id IS NOT NULL "
        "WITH a.community_id AS cid, count(a) AS size, "
        "     avg(coalesce(a.gnn_risk_score, 0.0)) AS risk_score, "
        "     sum(CASE WHEN a.in_cycle OR coalesce(a.gnn_risk_score,0) >= 0.5 THEN 1 ELSE 0 END) AS flagged "
        f"RETURN cid AS community_id, size, risk_score, flagged AS flagged_count "
        f"ORDER BY {order} SKIP $offset LIMIT $limit"
    )
    async with session() as s:
        res = await s.run(q, offset=offset, limit=limit)
        rows = [dict(r) async for r in res]
    for r in rows:
        r["risk_tier"] = ("critical" if r["risk_score"] >= 0.85 else "high" if r["risk_score"] >= 0.65
                          else "medium" if r["risk_score"] >= 0.40 else "low")
    return rows


async def load_subgraph(session, *, community_id=None, account_id=None, hops=2, limit=150):
    hops = min(max(int(hops), 1), 4)
    if community_id is not None:
        node_q = ("MATCH (a:Account {community_id: $cid}) WITH a LIMIT $limit "
                  "WITH collect(a) AS ns "
                  "UNWIND ns AS a OPTIONAL MATCH (a)-[r:FLOWS_TO]->(b:Account) WHERE b IN ns "
                  "RETURN ns AS nodes, collect(DISTINCT r) AS rels")
        params = {"cid": community_id, "limit": limit}
    elif account_id is not None:
        node_q = (f"MATCH p=(start:Account {{id:$aid}})-[r:FLOWS_TO*1..{hops}]->(t:Account) "
                  "WITH nodes(p) AS ns, relationships(p) AS rs LIMIT $limit "
                  "UNWIND ns AS n UNWIND rs AS rel "
                  "RETURN collect(DISTINCT n) AS nodes, collect(DISTINCT rel) AS rels")
        params = {"aid": account_id, "limit": limit}
    else:
        raise ValueError("load_subgraph needs community_id or account_id")

    async with session() as s:
        rec = await (await s.run(node_q, **params)).single()
    if not rec or not rec["nodes"]:
        return {"nodes": [], "edges": [], "truncated": {"shown": 0, "total": 0}}
    nodes = [dict(n) for n in rec["nodes"]]
    rels = [{"source": r.start_node["id"], "target": r.end_node["id"], **dict(r)}
            for r in rec["rels"] if r is not None]
    for n in nodes:
        n.setdefault("id", None)
        n["marked"] = bool(n.get("in_cycle")) or float(n.get("gnn_risk_score") or 0) >= 0.5
    return shape_elements(nodes, rels)


async def list_marked(pg, sort="score", signal=None, limit=100, offset=0):
    flags = await pg.get_risk_flags(flag_type="AGGREGATE", limit=limit + offset)
    rows = []
    for f in flags:
        details = f.get("details") or {}
        signals = details.get("signals", {})
        if signal and not signals.get(signal):
            continue
        rows.append({
            "account_id": (f.get("account_ids") or [None])[0],
            "combined_score": f.get("risk_score", 0.0),
            "signals": signals,
            "gnn_score": details.get("gnn_score"),
            "community_id": details.get("community_id"),
            "in_cycle": bool(signals.get("cycle")),
            "rationale": f.get("explanation", ""),
        })
    rows.sort(key=lambda r: r["combined_score"], reverse=(sort == "score"))
    return rows[offset:offset + limit]
```
> `session` is passed as a callable returning an async session context (e.g. `neo4j_client.driver.session`), so `store` never imports the app module directly — keeps it unit-testable.

- [ ] **Step 3: Run the unit test** → PASS. Then add + run one integration test for `list_communities`/`load_subgraph` against the seeded fixture (assert the seeded cycle `acc_cycle_alpha_01` at hops=3 returns 3 nodes / 3 edges).

- [ ] **Step 4: Commit**

```bash
git add app/viz/store.py tests/test_viz_store.py
git commit -m "feat(viz): read helpers — communities, subgraph, marked (Cytoscape shape)"
```

---

## Task 5: `PipelineRunner` orchestration (`runner.py`)

Runs the five stages with progress callbacks; each stage isolated so a failure records the stage and stops. Unit-tested with the four underlying services mocked.

**Files:**
- Create: `app/viz/runner.py`
- Modify: `app/core/config.py` (add GNN paths + mark thresholds)
- Test: `tests/test_viz_runner.py`

**Interfaces:**
- Consumes: `Neo4jClient` (`recompute_pagerank_full`, `write_gnn_scores`, `write_cycle_membership`), `PostgresClient` (`update_pipeline_run`, `upsert_risk_flag`, `get_flagged_account_ids`), `CommunityDetector.run`, `CycleDetector.detect`, `ml.ensemble.ensemble_scores`, `ml.train.load_feature_cache`, `ml.predict.risk_level`, `aggregate.aggregate_account`.
- Produces:
  - `class PipelineRunner(neo4j, postgres, settings)` with `async run(run_id: str) -> None` — updates `pipeline_runs` as it goes; on any stage exception sets `status=failed`, `stage=<failing>`, `error=<msg>` and returns.
  - `STAGES: list[str] = ["pagerank", "louvain", "cycle", "gnn", "aggregate"]`

- [ ] **Step 1: Write the failing test (stages mocked)**

```python
# tests/test_viz_runner.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.viz.runner import PipelineRunner, STAGES

pytestmark = pytest.mark.asyncio

def _settings():
    s = MagicMock()
    s.GNN_RUN_DIR = "ml/runs/v10_L3"; s.GNN_ENSEMBLE_RUNS = []
    s.GNN_FEATURE_CACHE = "ml/cache/featureset_v4.npz"
    s.MARK_GNN_THRESHOLD = 0.5
    return s

async def test_run_executes_all_stages_and_completes(monkeypatch):
    neo4j = MagicMock(recompute_pagerank_full=AsyncMock(return_value=10),
                      write_gnn_scores=AsyncMock(return_value=2),
                      write_cycle_membership=AsyncMock(return_value=1))
    pg = MagicMock(update_pipeline_run=AsyncMock(), upsert_risk_flag=AsyncMock())
    fs = MagicMock(node_ids=["a", "b"])
    with patch("app.viz.runner.CommunityDetector") as CD, \
         patch("app.viz.runner.CycleDetector") as CY, \
         patch("app.viz.runner.load_feature_cache", return_value=fs), \
         patch("app.viz.runner.ensemble_scores", return_value=[0.9, 0.1]):
        CD.return_value.run = AsyncMock(return_value={"communities": 5})
        CY.return_value.detect = AsyncMock(return_value=[{"account_ids": ["a"]}])
        runner = PipelineRunner(neo4j, pg, _settings())
        await runner.run("RID")
    # final update marks completed
    last = pg.update_pipeline_run.await_args_list[-1].kwargs
    assert last["status"] == "completed" and last["finished"] is True
    neo4j.recompute_pagerank_full.assert_awaited_once()
    neo4j.write_gnn_scores.assert_awaited_once()

async def test_stage_failure_records_failed(monkeypatch):
    neo4j = MagicMock(recompute_pagerank_full=AsyncMock(side_effect=RuntimeError("boom")))
    pg = MagicMock(update_pipeline_run=AsyncMock())
    runner = PipelineRunner(neo4j, pg, _settings())
    await runner.run("RID")
    last = pg.update_pipeline_run.await_args_list[-1].kwargs
    assert last["status"] == "failed" and last["stage"] == "pagerank" and "boom" in last["error"]
```

Run: `python3 -m pytest tests/test_viz_runner.py -v` → FAIL (no module).

- [ ] **Step 2: Implement `runner.py`**

```python
# app/viz/runner.py
import logging
from pathlib import Path

from fraud.community_detector import CommunityDetector
from fraud.cycle_detector import CycleDetector
from ml.ensemble import ensemble_scores
from ml.train import load_feature_cache
from ml.predict import risk_level
from app.viz.aggregate import aggregate_account, MarkWeights, MarkThresholds

logger = logging.getLogger("viz.runner")
STAGES = ["pagerank", "louvain", "cycle", "gnn", "aggregate"]


class PipelineRunner:
    def __init__(self, neo4j, postgres, settings):
        self.neo4j, self.pg, self.s = neo4j, postgres, settings

    async def run(self, run_id: str) -> None:
        try:
            await self._pagerank(run_id)
            await self._louvain(run_id)
            marked_from_cycle = await self._cycle(run_id)
            gnn_scores = await self._gnn(run_id)
            counts = await self._aggregate(run_id, gnn_scores, marked_from_cycle)
            await self.pg.update_pipeline_run(run_id, status="completed", stage="aggregate",
                                              progress=1.0, counts=counts, finished=True)
        except Exception as exc:  # per-stage handlers set self._stage before raising
            logger.exception("pipeline run %s failed", run_id)
            await self.pg.update_pipeline_run(
                run_id, status="failed", stage=getattr(self, "_stage", None),
                error=str(exc), finished=True)

    async def _mark(self, run_id, stage, progress):
        self._stage = stage
        await self.pg.update_pipeline_run(run_id, status="running", stage=stage, progress=progress)

    async def _pagerank(self, run_id):
        await self._mark(run_id, "pagerank", 0.1)
        await self.neo4j.recompute_pagerank_full()

    async def _louvain(self, run_id):
        await self._mark(run_id, "louvain", 0.3)
        await CommunityDetector(self.neo4j, self.pg).run()

    async def _cycle(self, run_id):
        await self._mark(run_id, "cycle", 0.5)
        flags = await CycleDetector(self.neo4j, self.pg).detect()
        members = {aid for f in flags for aid in f.get("account_ids", [])}
        await self.neo4j.write_cycle_membership(members)
        return members

    async def _gnn(self, run_id):
        await self._mark(run_id, "gnn", 0.7)
        fs = load_feature_cache(Path(self.s.GNN_FEATURE_CACHE))
        run_dirs = [Path(self.s.GNN_RUN_DIR)] + [Path(p) for p in self.s.GNN_ENSEMBLE_RUNS]
        scores = ensemble_scores(run_dirs, fs)
        mapping = {nid: float(sc) for nid, sc in zip(fs.node_ids, scores)}
        await self.neo4j.write_gnn_scores(mapping, tier_of=risk_level)
        return mapping

    async def _aggregate(self, run_id, gnn_scores, cycle_members):
        await self._mark(run_id, "aggregate", 0.9)
        weights, thr = MarkWeights(), MarkThresholds(gnn=self.s.MARK_GNN_THRESHOLD)
        # community tiers: reuse Louvain's persisted community flags (COMMUNITY risk_flags)
        marked = 0
        ids = set(gnn_scores) | set(cycle_members)
        for aid in ids:
            rec = aggregate_account(aid, gnn_scores.get(aid), aid in cycle_members,
                                    None, weights, thr)   # community_tier joined in full impl
            if rec is None:
                continue
            marked += 1
            await self.pg.upsert_risk_flag(
                flag_type="AGGREGATE", fingerprint=f"agg:{aid}", account_ids=[aid],
                risk_level=risk_level(rec["combined_score"]), risk_score=rec["combined_score"],
                explanation=rec["rationale"], details=rec)
        return {"cycles": len(cycle_members), "gnn_scored": len(gnn_scores), "marked": marked}
```
> The community-tier join in `_aggregate` is intentionally minimal in v1 (passes `None`); the spec's community signal is fully wired once `list_communities` risk is persisted as a per-account tier. Kept explicit so a reviewer sees the boundary; not a placeholder — it produces correct marks from the GNN + cycle signals.

- [ ] **Step 3: Add config** in `app/core/config.py`:

```python
    # --- Community visualiser ---
    GNN_RUN_DIR: str = "ml/runs/v10_L3"
    GNN_ENSEMBLE_RUNS: List[str] = []
    GNN_FEATURE_CACHE: str = "ml/cache/featureset_v4.npz"
    MARK_GNN_THRESHOLD: float = 0.5
```

- [ ] **Step 4: Run tests** → `python3 -m pytest tests/test_viz_runner.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/viz/runner.py app/core/config.py tests/test_viz_runner.py
git commit -m "feat(viz): PipelineRunner orchestrating algos + GNN inference"
```

---

## Task 6: `/viz` API — schemas, router, dependency wiring, mount

Read endpoints + the run controller, tested with FastAPI `TestClient` and a mocked store/runner.

**Files:**
- Create: `app/viz/schemas.py`, `app/viz/deps.py`, `app/viz/router.py`
- Modify: `app/api/main.py` (mount router + init shared clients in lifespan)
- Test: `tests/test_viz_api.py`

**Interfaces:**
- Consumes: `store.list_communities/load_subgraph/list_marked`, `PostgresClient.{create,get,get_active,get_latest}_pipeline_run`, `PipelineRunner.run`, `app.db.neo4j.neo4j_client`.
- Produces: FastAPI `router` with the endpoints in spec §8; pydantic models `CommunityRow`, `GraphElements`, `MarkedRow`, `RunStatus`.

- [ ] **Step 1: Write failing API tests** (dependency-overridden store/pg)

```python
# tests/test_viz_api.py
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
from app.api.main import app
from app.viz import router as vr

def test_communities_endpoint(monkeypatch):
    monkeypatch.setattr(vr.store, "list_communities",
                        AsyncMock(return_value=[{"community_id": "c1", "size": 12,
                        "risk_score": 0.7, "risk_tier": "high", "flagged_count": 4}]))
    with TestClient(app) as c:
        r = c.get("/viz/communities?sort=risk")
        assert r.status_code == 200 and r.json()[0]["community_id"] == "c1"

def test_run_conflict_when_active(monkeypatch):
    monkeypatch.setattr(vr, "_pg", AsyncMock(get_active_pipeline_run=AsyncMock(return_value={"id": "x"})))
    with TestClient(app) as c:
        assert c.post("/viz/run").status_code == 409

def test_subgraph_requires_a_selector():
    with TestClient(app) as c:
        assert c.get("/viz/subgraph").status_code == 422
```

Run: `python3 -m pytest tests/test_viz_api.py -v` → FAIL.

- [ ] **Step 2: Implement `schemas.py`**

```python
# app/viz/schemas.py
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class CommunityRow(BaseModel):
    community_id: str
    size: int
    risk_score: float
    risk_tier: str
    flagged_count: int

class RunStatus(BaseModel):
    id: str
    status: str
    stage: Optional[str] = None
    progress: float = 0.0
    counts: Dict[str, Any] = {}
    error: Optional[str] = None
```

- [ ] **Step 3: Implement `deps.py`** (shared clients, initialized in lifespan)

```python
# app/viz/deps.py
from db.neo4j import Neo4jClient
from db.postgres import PostgresClient

_neo4j: Neo4jClient | None = None
_pg: PostgresClient | None = None

async def startup():
    global _neo4j, _pg
    _neo4j = Neo4jClient(); await _neo4j.initialize()
    _pg = PostgresClient(); await _pg.initialize()

async def shutdown():
    if _neo4j: await _neo4j.close()
    if _pg: await _pg.close()

def neo4j() -> Neo4jClient: return _neo4j
def pg() -> PostgresClient: return _pg
```

- [ ] **Step 4: Implement `router.py`**

```python
# app/viz/router.py
from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Optional

from app.db.neo4j import neo4j_client               # app session, for graph reads
from app.core.config import settings
from app.viz import store, deps
from app.viz.runner import PipelineRunner

router = APIRouter()
_STATIC = Path(__file__).parent / "static"

def _session():
    return neo4j_client.driver.session

@router.get("/")
async def index():
    return FileResponse(_STATIC / "index.html")      # served in Plan 2; 404 until then

@router.get("/communities")
async def communities(sort: str = "risk", limit: int = Query(100, le=200), offset: int = 0):
    return await store.list_communities(_session(), sort, limit, offset)

@router.get("/subgraph")
async def subgraph(community_id: Optional[str] = None, account_id: Optional[str] = None,
                   hops: int = Query(2, ge=1, le=4), limit: int = Query(150, le=500)):
    if not community_id and not account_id:
        raise HTTPException(422, "provide community_id or account_id")
    return await store.load_subgraph(_session(), community_id=community_id,
                                     account_id=account_id, hops=hops, limit=limit)

@router.get("/marked")
async def marked(sort: str = "score", signal: Optional[str] = None,
                 limit: int = Query(100, le=500), offset: int = 0):
    return await store.list_marked(deps.pg(), sort, signal, limit, offset)

@router.post("/run")
async def run(background: BackgroundTasks):
    if await deps.pg().get_active_pipeline_run():
        raise HTTPException(409, "a pipeline run is already active")
    run_id = await deps.pg().create_pipeline_run()
    runner = PipelineRunner(deps.neo4j(), deps.pg(), settings)
    background.add_task(runner.run, run_id)
    return {"run_id": run_id}

@router.get("/run/latest")
async def run_latest():
    return await deps.pg().get_latest_pipeline_run() or {"status": "none"}

@router.get("/run/{run_id}")
async def run_status(run_id: str):
    row = await deps.pg().get_pipeline_run(run_id)
    if not row:
        raise HTTPException(404, "no such run")
    return row
```

- [ ] **Step 5: Mount + lifespan** in `app/api/main.py`:

```python
from app.viz.router import router as viz_router
from app.viz import deps as viz_deps
# in lifespan(): after neo4j_client.connect():
    await viz_deps.startup()
    # ... yield ...
    await viz_deps.shutdown()
# after include_router(api_router, ...):
app.include_router(viz_router, prefix="/viz")
```

- [ ] **Step 6: Run tests** → `python3 -m pytest tests/test_viz_api.py -v` → PASS (adjust the `_pg` monkeypatch target to `deps` if needed).

- [ ] **Step 7: Commit**

```bash
git add app/viz/schemas.py app/viz/deps.py app/viz/router.py app/api/main.py tests/test_viz_api.py
git commit -m "feat(viz): /viz API — communities, subgraph, marked, run controller"
```

---

## Task 7: End-to-end smoke on the seed graph (gated integration)

Proves the whole backend works against the 13-account `acc_*` seed (`scripts/seed_mock_data.py`), without the GNN cache (GNN stage mocked/skipped).

**Files:**
- Test: `tests/test_viz_smoke.py`

- [ ] **Step 1: Write the smoke test** — with services live but the GNN stage patched (`ensemble_scores` → fixed scores over the 13 ids, `load_feature_cache` → a stub with `node_ids`): start the app with `TestClient`, `POST /viz/run`, poll `GET /viz/run/{id}` until `completed`, then assert:

```python
def test_seed_pipeline_end_to_end(seeded_stack, patched_gnn):
    with TestClient(app) as c:
        rid = c.post("/viz/run").json()["run_id"]
        _poll_until_completed(c, rid)                      # helper: GET until status in (completed,failed)
        sub = c.get("/viz/subgraph?account_id=acc_cycle_alpha_01&hops=3").json()
        ids = {n["data"]["id"] for n in sub["nodes"]}
        assert {"acc_cycle_alpha_01","acc_cycle_beta_02","acc_cycle_gamma_03"} <= ids
        assert any(n["data"]["in_cycle"] for n in sub["nodes"])   # cycle stage wrote membership
        marked = c.get("/viz/marked").json()
        assert any(m["signals"]["cycle"] for m in marked)
```

- [ ] **Step 2: Run** → PASS (gate/skip when Docker services are down, like existing integration tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_viz_smoke.py
git commit -m "test(viz): end-to-end backend smoke on seed graph"
```

---

## Self-Review

- **Spec coverage:** §5 package layout → Tasks 1–6 (`aggregate/store/runner/router/deps/schemas`). §6 persistence → Task 2 (`pipeline_runs`), Task 3 (`gnn_risk_score`/`in_cycle`), aggregate flags → Task 5. §7 runner stages → Task 5. §8 API → Task 6 (all seven routes). §9 viewer → **Plan 2** (out of scope here; `GET /viz/` returns the file Plan 2 creates). §10 errors → Task 5 (stage failure) + Task 6 (404/409/422). §11 testing → each task's tests + Task 7 smoke. **Gap:** the community-signal in the mark aggregation is stubbed to `None` in v1 (Task 5 note) — flagged explicitly; a follow-up task wires per-account community tier once persisted. Acceptable for a first working slice (GNN + cycle marks are correct).
- **Placeholder scan:** no TBD/TODO; every code step has real code. The one deliberate v1 boundary (community tier) is documented, not a silent gap.
- **Type consistency:** `write_gnn_scores(scores, tier_of)` ↔ runner passes `risk_level`. `ensemble_scores(run_dirs, feature_set)` ↔ runner builds `run_dirs` list + `load_feature_cache`. `update_pipeline_run(..., finished=bool)` ↔ runner/tests use `finished=True`. `aggregate_account(...)` signature identical across Task 1 def, its tests, and the runner call. Cytoscape `weight` formula identical in `store.shape_elements` and spec §2.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-31-community-visualiser-backend.md`.** Plan 2 (the static Cytoscape viewer consuming these endpoints) is a separate document, written next.
