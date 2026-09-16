# Showcase Backend (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the frontend real, cached, read-only data for every view (stats, alerts, transactions, corrected graph reads, pipeline aliases) and replace the AI stub + broken agent with one pluggable explanation service.

**Architecture:** New thin services under `app/services/` read Neo4j (`TRANSFER`/`FLOWS_TO`/`Account`) and Postgres (`risk_flags`, `pipeline_runs`, new `app_meta`). Expensive whole-graph aggregation happens once in an in-process `StatsCache` warmed at startup. The `/viz` JSON routes are re-mounted under `/api/pipeline`. `ExplanationService` gathers evidence from the stores and asks a provider (Ollama local model → Anthropic API → rule-based) for a schema-validated explanation.

**Tech Stack:** Python 3.9, FastAPI 0.115, Pydantic 2.8, neo4j 5.21 async driver, asyncpg, `anthropic` (0.x), `ollama`, `httpx`, pytest.

**Spec:** `Backend/docs/superpowers/specs/2026-09-15-showcase-and-ship-design.md` — this plan implements §2 (facts), §5, §6, §7 and the backend half of §11. Plans B (frontend), C (snapshot + upload) and D (docs/CI/deploy) follow.

## Global Constraints

- **Python 3.9 everywhere** (system and `.venv`). Use `Optional[X]`, `List[X]`, `Dict[K, V]`, `Tuple[...]` from `typing`. Never `X | None`, never `list[str]`, never `match`.
- All commands run from `/Users/kavinnimalarajan/FlowGraph/Backend` unless stated.
- Tests: `python3 -m pytest <file> -q -p no:cacheprovider`. The full suite (`python3 -m pytest -q -p no:cacheprovider`) must stay green: 490 passed / 8 skipped at the start of this plan.
- Live stack (for gated tests and smoke): Docker containers `flowgraph-neo4j`, `flowgraph-postgres`, `flowgraph-redis` are up. Run anything that touches Postgres with
  `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph'` (the root `config.py` default password is wrong for the container).
- Cypher uses named parameters only; whitelisted identifiers may be interpolated only after validation (existing convention in `app/viz/store.py`).
- Every commit ends with this trailer. Define it once per shell, then pass `-m "$TRAILER"`:
  ```bash
  TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
  ```
- Branch: `showcase-and-ship`. Never commit `Backend/.env`, `uploads/`, `ml/runs/`, `ml/cache/`.
- Existing tests patch `app.viz.deps.startup/shutdown` to `AsyncMock` and `app.viz.router.PipelineRunner` by name — keep those names importable.

## File structure

| File | Responsibility |
|---|---|
| `app/schemas/api.py` (new) | Pydantic response models for the new endpoints |
| `app/services/stats_service.py` (new) | `StatsCache` + pure series math; module-level `cache`, `warmup`, `invalidate` |
| `app/services/alerts_service.py` (new) | shape `risk_flags` rows → alerts; status update |
| `app/services/transactions_service.py` (new) | `TRANSFER` reads (latest / per account), tier + flagged join |
| `app/services/explanation_service.py` (new) | evidence gathering, providers, selection, rule-based fallback |
| `app/services/ai_enrichment.py` (rewrite) | one-line delegate to `explanation_service` (keeps `RiskAggregator` import stable) |
| `app/services/graph_service.py` (modify) | GNN-score coalescing, richer `NodeData`, flow over `TRANSFER`, `get_account` |
| `app/services/risk_aggregator.py` (modify) | none — but its low-confidence path now receives an object with attributes (bug fix by construction) |
| `app/api/endpoints.py` (modify) | remove agent endpoint; add stats/alerts/transactions/account/system routes |
| `app/api/main.py` (modify) | mount viz api at `/api/pipeline`; warm the stats cache; configure the explanation provider |
| `app/viz/router.py` (modify) | split into `pages` + `api` sub-routers; add `/metrics/curve` |
| `app/viz/deps.py` (modify) | call `init_constraints()` and `ensure_app_meta_table()` at startup |
| `app/viz/runner.py` (modify) | invalidate the stats cache after aggregate |
| `app/core/config.py` (modify) | `LLM_PROVIDER`, `LLM_MODEL`, `OLLAMA_BASE_URL` |
| `db/neo4j.py` (modify) | `init_constraints`: relationship index `transfer_ts`, node index `account_community` |
| `db/postgres.py` (modify) | `get_risk_flags(offset)`, `count_risk_flags`, `update_risk_flag_status`, `count_open_flags_by_type`, `get_account_ids_for_flag_type`, `ensure_app_meta_table`, `get_app_meta`, `set_app_meta` |
| `migrations/004_create_app_meta_table.sql` (new) | key/value JSONB table, seeded with the IBM dataset row |
| `scripts/purge_mock_seed.py` (new) | delete the 13 `acc_*` seed nodes + their Redis keys |
| `requirements.txt` (modify) | drop `pydantic-ai`; add `anthropic`, `ollama`, `httpx` |
| `app/agent/` (delete) | — |
| tests: `tests/test_stats_service.py`, `tests/test_alerts_api.py`, `tests/test_transactions_service.py`, `tests/test_graph_service.py`, `tests/test_explanation_service.py`, `tests/test_pipeline_alias.py`, `tests/test_postgres_meta.py`, `tests/test_api_smoke.py` | |

---

### Task 1: Remove the agent package and the business-summary endpoint

**Files:**
- Delete: `app/agent/__init__.py`, `app/agent/helpers.py`, `app/agent/tools.py`
- Modify: `app/api/endpoints.py` (remove `_fallback_business_summary`, `summarize_business`, the `BusinessSummary*` imports)
- Modify: `app/schemas/graph.py` (remove `BusinessSummaryRequest`, `BusinessSummaryResponse`)
- Modify: `app/services/graph_service.py` (remove `get_risky_accounts`, `get_business_risk_summary`)
- Modify: `requirements.txt`

**Interfaces:**
- Produces: nothing new; the app must import and serve without `pydantic_ai`.

- [ ] **Step 1: Confirm nothing else depends on the agent**

Run: `grep -rn "app.agent\|get_risky_accounts\|get_business_risk_summary\|BusinessSummary" --include="*.py" . | grep -v "^./app/agent/"`
Expected: only lines in `app/api/endpoints.py`, `app/schemas/graph.py`, `app/services/graph_service.py`.

- [ ] **Step 2: Delete the package and the dead code**

```bash
git rm -q app/agent/__init__.py app/agent/helpers.py app/agent/tools.py
```

In `app/api/endpoints.py` delete `_fallback_business_summary` and the whole `summarize_business` route, and reduce the schema import to:

```python
from app.schemas.graph import GraphElements, FlowSummaryResponse, AIReportResponse
```

Also drop the now-unused `from typing import Optional, Dict, Any` down to `from typing import Optional`.

In `app/schemas/graph.py` delete the `BusinessSummaryRequest` and `BusinessSummaryResponse` classes.

In `app/services/graph_service.py` delete the `get_risky_accounts` and `get_business_risk_summary` classmethods (everything from `@classmethod\n    async def get_risky_accounts` up to the line before `@classmethod\n    async def get_subgraph`).

- [ ] **Step 3: Update requirements**

Replace the line `pydantic-ai>=0.0.14` in `requirements.txt` with:

```
# Explanation providers (app/services/explanation_service.py). anthropic 1.x needs
# Python >= 3.10 — stay on 0.x while the project runs on 3.9.
anthropic>=0.40,<1
ollama>=0.4,<1
httpx>=0.27
```

Run: `python3 -m pip install "anthropic>=0.40,<1" "ollama>=0.4,<1" "httpx>=0.27"`
Expected: both packages import: `python3 -c "import anthropic, ollama; print(anthropic.__version__, ollama.__version__)"`

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest -q -p no:cacheprovider`
Expected: 490 passed, 8 skipped (unchanged — nothing tested the agent).

- [ ] **Step 5: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add -A app/agent app/api/endpoints.py app/schemas/graph.py app/services/graph_service.py requirements.txt
git commit -m "refactor(api): remove the Ollama agent and business-summary endpoint" -m "The agent used 3.10-only syntax on a 3.9 runtime (import failed, swallowed) and matched business_id, which only the mock seed accounts carry. The explanation service replaces it." -m "$TRAILER"
```

---

### Task 2: Neo4j indexes for transaction and community reads

**Files:**
- Modify: `db/neo4j.py:72-98` (`init_constraints`)
- Modify: `app/viz/deps.py:17-25` (`startup`)

**Interfaces:**
- Produces: index `transfer_ts` on `TRANSFER(ts)`, index `account_community` on `Account(community_id)`; `viz_deps.startup()` now applies constraints.

- [ ] **Step 1: Add the two index statements**

In `db/neo4j.py`, replace the `constraints = [...]` list inside `init_constraints` with:

```python
        constraints = [
            "CREATE CONSTRAINT account_id_unique IF NOT EXISTS "
            "FOR (a:Account) REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT transfer_txn_unique IF NOT EXISTS "
            "FOR ()-[t:TRANSFER]-() REQUIRE t.txn_id IS UNIQUE",
            # "Latest N transactions" orders 5M TRANSFER edges by ts — a full sort
            # without this relationship index (~8 s per request with it absent).
            "CREATE INDEX transfer_ts IF NOT EXISTS "
            "FOR ()-[t:TRANSFER]-() ON (t.ts)",
            # Community lookups (viz subgraph by community, explanation evidence)
            # otherwise scan every Account.
            "CREATE INDEX account_community IF NOT EXISTS "
            "FOR (a:Account) ON (a.community_id)",
        ]
```

- [ ] **Step 2: Apply constraints at API startup**

In `app/viz/deps.py` change `startup` to:

```python
async def startup() -> None:
    global _neo4j, _pg
    _neo4j = Neo4jClient()
    await _neo4j.initialize()
    await _neo4j.init_constraints()   # idempotent; adds the ts/community indexes
    _pg = PostgresClient()
    await _pg.initialize()
    from app.viz import truth
    truth.preload()      # parse ground-truth labels once, off the request path
```

- [ ] **Step 3: Apply live and verify the index exists**

Run:
```bash
python3 - <<'EOF'
import asyncio
from db.neo4j import Neo4jClient
async def main():
    c = Neo4jClient(); await c.initialize(); await c.init_constraints()
    async with c.driver.session() as s:
        rows = [r["name"] async for r in await s.run("SHOW INDEXES YIELD name")]
    print(sorted(rows)); await c.close()
asyncio.run(main())
EOF
```
Expected: the list contains `account_community` and `transfer_ts` (population runs in the background; `SHOW INDEXES YIELD name, state` shows `ONLINE` within a minute or two on the 5M-edge graph).

- [ ] **Step 4: Run the suite and commit**

Run: `python3 -m pytest -q -p no:cacheprovider` — Expected: 490 passed, 8 skipped.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add db/neo4j.py app/viz/deps.py
git commit -m "feat(db): index TRANSFER.ts and Account.community_id; apply constraints at API startup" -m "$TRAILER"
```

---

### Task 3: Purge the mock seed accounts from the real graph

**Files:**
- Create: `scripts/purge_mock_seed.py`

**Interfaces:**
- Consumes: `scripts/seed_mock_data.py` account list (ids all start with `acc_`).
- Produces: a graph with no `acc_*` accounts and no `edge:acc_*` Redis keys.

- [ ] **Step 1: Write the script**

```python
"""Remove the demo accounts written by scripts/seed_mock_data.py.

The 13 `acc_*` accounts carry 2026 timestamps and a hand-set risk_score, so they
pollute dataset-anchored stats and the snapshot. Deletes their nodes (with all
relationships) and their `edge:acc_*` Redis keys. Safe to re-run.

    python3 scripts/purge_mock_seed.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import NEO4J_DATABASE
from db.neo4j import Neo4jClient
from db.redis import RedisClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("purge_mock_seed")

DELETE_Q = (
    "MATCH (a:Account) WHERE a.id STARTS WITH 'acc_' "
    "WITH a LIMIT 1000 DETACH DELETE a RETURN count(*) AS n"
)


async def purge() -> int:
    neo4j = Neo4jClient()
    await neo4j.initialize()
    deleted = 0
    try:
        async with neo4j.driver.session(database=NEO4J_DATABASE) as s:
            while True:
                rec = await (await s.run(DELETE_Q)).single()
                n = int(rec["n"]) if rec else 0
                deleted += n
                if n == 0:
                    break
    finally:
        await neo4j.close()
    log.info("deleted %d mock accounts", deleted)

    redis = RedisClient()
    await redis.initialize()
    removed = 0
    try:
        async for key in redis.client.scan_iter(match="edge:acc_*", count=1000):
            await redis.client.delete(key)
            removed += 1
    finally:
        await redis.close()
    log.info("removed %d mock redis keys", removed)
    return deleted


if __name__ == "__main__":
    asyncio.run(purge())
```

- [ ] **Step 2: Check `RedisClient` exposes `.client` and `initialize/close`**

Run: `grep -n "self.client\|async def initialize\|async def close" db/redis.py | head`
Expected: all three present (they are used the same way by `run_ingest._reset_stores`). If the attribute is named differently, use that name in the script.

- [ ] **Step 3: Run it live and verify**

Run: `python3 scripts/purge_mock_seed.py`
Expected: `deleted 13 mock accounts` and `removed 11 mock redis keys`.

Run:
```bash
python3 - <<'EOF'
import asyncio
from neo4j import AsyncGraphDatabase
async def main():
    d = AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("neo4j","changeme"))
    async with d.session() as s:
        print("acc_* left:", (await (await s.run("MATCH (a:Account) WHERE a.id STARTS WITH 'acc_' RETURN count(a) AS n")).single())["n"])
        r = await (await s.run("MATCH (:Account)-[f:FLOWS_TO]->(:Account) RETURN max(f.last_ts) AS mx")).single()
        print("max FLOWS_TO.last_ts:", r["mx"])
    await d.close()
asyncio.run(main())
EOF
```
Expected: `acc_* left: 0` and `max FLOWS_TO.last_ts: 1663517880` (the dataset end, no longer a 2026 value).

- [ ] **Step 4: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add scripts/purge_mock_seed.py
git commit -m "chore(scripts): purge_mock_seed removes the acc_* demo accounts from the real graph" -m "$TRAILER"
```

---

### Task 4: Postgres — `app_meta` table and the new `risk_flags` helpers

**Files:**
- Create: `migrations/004_create_app_meta_table.sql`
- Modify: `db/postgres.py` (`get_risk_flags`; new methods after `get_flagged_account_ids`)
- Modify: `app/viz/deps.py` (`startup` calls `ensure_app_meta_table`)
- Test: `tests/test_postgres_meta.py` (gated on the live stack)

**Interfaces:**
- Produces (all on `PostgresClient`):
  - `get_risk_flags(flag_type=None, min_level=None, status=None, limit=100, offset=0) -> List[Dict]`
  - `count_risk_flags(flag_type=None, min_level=None, status=None) -> int`
  - `update_risk_flag_status(flag_id: int, status: str) -> Optional[Dict]` (None if no such id)
  - `count_open_flags_by_type() -> Dict[str, int]`
  - `get_account_ids_for_flag_type(flag_type: str, status: str = "open") -> List[str]`
  - `ensure_app_meta_table() -> None`, `get_app_meta(key: str) -> Optional[Dict]`, `set_app_meta(key: str, value: Dict) -> None`

- [ ] **Step 1: Write the migration**

`migrations/004_create_app_meta_table.sql`:

```sql
-- Migration 004: app_meta — small key/value store for application state that is
-- not payment data (which dataset is loaded, when it was ingested). Written
-- directly, outside the outbox convention, like pipeline_runs.

CREATE TABLE IF NOT EXISTS app_meta (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the dataset row for the IBM AML HI-Small graph loaded by
-- ml/datasets/run_ingest.py. start/end are the measured TRANSFER.ts bounds.
INSERT INTO app_meta (key, value) VALUES (
    'dataset',
    '{"name": "IBM AML HI-Small", "source": "ibm-hi-small", "labelled": true,
      "start_ts": 1661990400, "end_ts": 1663517880, "rows": null, "uploaded_at": null}'::jsonb
) ON CONFLICT (key) DO NOTHING;
```

- [ ] **Step 2: Write the gated test**

`tests/test_postgres_meta.py`:

```python
"""Live-Postgres tests for the app_meta helpers and the risk_flags additions.

Skips unless the dev container is reachable via POSTGRES_DSN (see the module
docstring of tests/test_viz_smoke.py for the exact value).
"""
import asyncio
import pytest


async def _client():
    from db.postgres import PostgresClient
    pg = PostgresClient()
    await pg.initialize()
    return pg


def _run(coro_fn):
    async def wrapper():
        try:
            pg = await _client()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Postgres not reachable: {exc}")
        try:
            return await coro_fn(pg)
        finally:
            await pg.close()
    return asyncio.run(wrapper())


def test_app_meta_roundtrip():
    async def fn(pg):
        await pg.ensure_app_meta_table()
        await pg.set_app_meta("test_key", {"a": 1, "b": [1, 2]})
        got = await pg.get_app_meta("test_key")
        await pg.set_app_meta("test_key", {"a": 2})
        again = await pg.get_app_meta("test_key")
        missing = await pg.get_app_meta("no_such_key")
        async with pg._get_connection() as conn:
            await conn.execute("DELETE FROM app_meta WHERE key = 'test_key'")
        return got, again, missing
    got, again, missing = _run(fn)
    assert got == {"a": 1, "b": [1, 2]}
    assert again == {"a": 2}          # set overwrites
    assert missing is None


def test_dataset_row_is_seeded():
    async def fn(pg):
        await pg.ensure_app_meta_table()
        return await pg.get_app_meta("dataset")
    ds = _run(fn)
    assert ds["source"] == "ibm-hi-small" and ds["end_ts"] == 1663517880


def test_risk_flag_paging_count_and_status():
    async def fn(pg):
        fp = "test:meta:paging"
        await pg.upsert_risk_flag(flag_type="TESTTYPE", fingerprint=fp, account_ids=["t1"],
                                  risk_level="high", risk_score=0.9, explanation="test row")
        total = await pg.count_risk_flags(flag_type="TESTTYPE")
        page0 = await pg.get_risk_flags(flag_type="TESTTYPE", limit=1, offset=0)
        page9 = await pg.get_risk_flags(flag_type="TESTTYPE", limit=1, offset=99)
        by_type = await pg.count_open_flags_by_type()
        ids = await pg.get_account_ids_for_flag_type("TESTTYPE")
        updated = await pg.update_risk_flag_status(page0[0]["id"], "reviewed")
        gone = await pg.update_risk_flag_status(-1, "reviewed")
        async with pg._get_connection() as conn:
            await conn.execute("DELETE FROM risk_flags WHERE fingerprint = $1", fp)
        return total, page0, page9, by_type, ids, updated, gone
    total, page0, page9, by_type, ids, updated, gone = _run(fn)
    assert total == 1 and len(page0) == 1 and page9 == []
    assert by_type.get("TESTTYPE") == 1
    assert ids == ["t1"]
    assert updated["status"] == "reviewed" and gone is None
```

- [ ] **Step 3: Run it to see it fail**

Run: `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' python3 -m pytest tests/test_postgres_meta.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'PostgresClient' object has no attribute 'ensure_app_meta_table'`.

- [ ] **Step 4: Implement the helpers**

In `db/postgres.py`, change the signature and the tail of `get_risk_flags`:

```python
    async def get_risk_flags(
        self,
        flag_type: Optional[str] = None,
        min_level: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
```

and replace the block from `params.append(limit)` to the end of the method with:

```python
        params.append(limit)
        limit_idx = len(params)
        params.append(offset)
        offset_idx = len(params)
        where = " AND ".join(conditions)
        query = f"""
        SELECT id, flag_type, fingerprint, account_ids, risk_level, risk_score,
               explanation, details, status, first_detected_at, last_detected_at,
               detection_count, created_at
        FROM risk_flags
        WHERE {where}
        ORDER BY last_detected_at DESC, id DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
        """

        async with self._get_connection() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    @staticmethod
    def _flag_conditions(
        flag_type: Optional[str], min_level: Optional[str], status: Optional[str]
    ) -> Tuple[str, List[Any]]:
        """Shared WHERE clause for the list/count pair (same filters, same order)."""
        _level_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        conditions = [
            "CASE risk_level "
            "WHEN 'low' THEN 1 WHEN 'medium' THEN 2 "
            "WHEN 'high' THEN 3 WHEN 'critical' THEN 4 ELSE 0 END >= $1"
        ]
        params: List[Any] = [_level_order.get(min_level or "low", 1)]
        if flag_type:
            params.append(flag_type)
            conditions.append(f"flag_type = ${len(params)}")
        if status:
            params.append(status)
            conditions.append(f"status = ${len(params)}")
        return " AND ".join(conditions), params

    async def count_risk_flags(
        self,
        flag_type: Optional[str] = None,
        min_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        where, params = self._flag_conditions(flag_type, min_level, status)
        async with self._get_connection() as conn:
            return int(await conn.fetchval(f"SELECT count(*) FROM risk_flags WHERE {where}", *params))

    async def update_risk_flag_status(self, flag_id: int, status: str) -> Optional[Dict[str, Any]]:
        """Analyst workflow: open → reviewed / dismissed / escalated. Returns the
        updated row, or None when no flag has that id."""
        query = """
        UPDATE risk_flags SET status = $2 WHERE id = $1
        RETURNING id, flag_type, fingerprint, account_ids, risk_level, risk_score,
                  explanation, details, status, first_detected_at, last_detected_at,
                  detection_count, created_at
        """
        async with self._get_connection() as conn:
            row = await conn.fetchrow(query, flag_id, status)
        return dict(row) if row else None

    async def count_open_flags_by_type(self) -> Dict[str, int]:
        async with self._get_connection() as conn:
            rows = await conn.fetch(
                "SELECT flag_type, count(*) AS n FROM risk_flags WHERE status = 'open' GROUP BY flag_type"
            )
        return {r["flag_type"]: int(r["n"]) for r in rows}

    async def get_account_ids_for_flag_type(self, flag_type: str, status: str = "open") -> List[str]:
        """Distinct account ids carrying an open flag of one detector type (the
        stats cache uses the AGGREGATE set to mark transactions as flagged)."""
        query = """
        SELECT DISTINCT unnest(account_ids) AS account_id
        FROM risk_flags WHERE flag_type = $1 AND status = $2 ORDER BY account_id
        """
        async with self._get_connection() as conn:
            rows = await conn.fetch(query, flag_type, status)
        return [r["account_id"] for r in rows]

    # ==================== APP META (key/value) ====================

    async def ensure_app_meta_table(self) -> None:
        """Apply migration 004 (idempotent) — same self-applying convention the
        detectors use for 002."""
        sql = (Path(__file__).resolve().parent.parent / "migrations"
               / "004_create_app_meta_table.sql").read_text()
        async with self._get_connection() as conn:
            await conn.execute(sql)

    async def get_app_meta(self, key: str) -> Optional[Dict[str, Any]]:
        async with self._get_connection() as conn:
            raw = await conn.fetchval("SELECT value FROM app_meta WHERE key = $1", key)
        if raw is None:
            return None
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    async def set_app_meta(self, key: str, value: Dict[str, Any]) -> None:
        async with self._get_connection() as conn:
            await conn.execute(
                "INSERT INTO app_meta (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                key, json.dumps(value),
            )
```

Make sure the imports at the top of `db/postgres.py` include `Tuple` and `Path`:

```python
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
```

(`json` is already imported — `_row_to_run` uses it.) Note `get_risk_flags` still builds its own conditions inline (unchanged text) — keep it that way to avoid touching the tested detector path; `count_risk_flags` uses the shared helper, which is a copy of the same logic.

- [ ] **Step 5: Ensure the table at startup**

In `app/viz/deps.py` `startup`, after `await _pg.initialize()` add:

```python
    await _pg.ensure_app_meta_table()   # migration 004, idempotent
```

- [ ] **Step 6: Run the tests**

Run: `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' python3 -m pytest tests/test_postgres_meta.py -q -p no:cacheprovider`
Expected: 3 passed.

Run: `python3 -m pytest -q -p no:cacheprovider` — Expected: 493 passed / 8 skipped, or 490 passed / 11 skipped when `POSTGRES_DSN` is not exported.

- [ ] **Step 7: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add migrations/004_create_app_meta_table.sql db/postgres.py app/viz/deps.py tests/test_postgres_meta.py
git commit -m "feat(db): app_meta key/value table; risk_flags paging, counts and status update" -m "$TRAILER"
```

---

### Task 5: Response schemas for the new endpoints

**Files:**
- Create: `app/schemas/api.py`

**Interfaces:**
- Produces: `AlertOut`, `AlertPage`, `AlertStatusUpdate`, `TransactionOut`, `TransactionPage`, `LLMStatus`, `ModelJSON`, `ExplanationOut`, `RISK_LEVELS`, `TYPOLOGIES`.

- [ ] **Step 1: Write the module**

```python
"""Pydantic response/request models for the showcase read API (spec §6, §7)."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

RISK_LEVELS = ("low", "medium", "high", "critical")
FLAG_STATUSES = ("open", "reviewed", "dismissed", "escalated")
TYPOLOGIES = ("CYCLE", "FAN-OUT", "FAN-IN", "GATHER-SCATTER", "SCATTER-GATHER",
              "BIPARTITE", "STACK", "RANDOM")


class AlertOut(BaseModel):
    id: int
    flag_type: str
    risk_level: str
    risk_score: float
    explanation: str
    account_ids: List[str]
    primary_account: Optional[str]
    details: Dict[str, Any] = Field(default_factory=dict)
    status: str
    first_detected_at: Optional[str]
    last_detected_at: Optional[str]
    detection_count: int


class AlertPage(BaseModel):
    total: int
    items: List[AlertOut]


class AlertStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(open|reviewed|dismissed|escalated)$")


class TransactionOut(BaseModel):
    txn_id: str
    sender_id: str
    receiver_id: str
    amount_cents: int
    currency: str
    rail: str
    event_type: Optional[str]
    ts: int
    sender_tier: str
    receiver_tier: str
    risk_tier: str
    flagged: bool


class TransactionPage(BaseModel):
    items: List[TransactionOut]


class LLMStatus(BaseModel):
    provider: str            # anthropic | ollama | none
    model: Optional[str]
    reachable: bool


class ModelJSON(BaseModel):
    """What a language model must return — the schema handed to the provider."""
    risk_level: str = Field(..., pattern="^(low|medium|high|critical)$")
    confidence: float = Field(..., ge=0.0, le=1.0)
    explanation: str = Field(..., min_length=1, max_length=1200)
    detected_typology: Optional[str] = None
    compliance_summary: str = Field(..., min_length=1, max_length=400)


class ExplanationOut(ModelJSON):
    """The API's explanation record: the model's fields plus provenance."""
    account_id: str
    provider: str
    model: Optional[str]
    generated_at: int
```

- [ ] **Step 2: Verify it imports and validates**

Run: `python3 -c "from app.schemas.api import ExplanationOut; print(ExplanationOut(account_id='a', provider='none', model=None, generated_at=1, risk_level='high', confidence=0.7, explanation='x', compliance_summary='y').risk_level)"`
Expected: `high`.

- [ ] **Step 3: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/schemas/api.py
git commit -m "feat(api): response schemas for alerts, transactions, explanations" -m "$TRAILER"
```

---

### Task 6: Stats — pure series math

**Files:**
- Create: `app/services/stats_service.py` (pure functions only in this task)
- Test: `tests/test_stats_service.py`

**Interfaces:**
- Produces: `BUCKET_BASE = 1800`, `PERIODS`, `currency_code(rail) -> str`, `build_series(hist, start_ts, end_ts, bucket_seconds) -> Dict`, `period_window(period, dataset_start, dataset_end) -> Tuple[int, int, int]`.
- `hist` is `Dict[int, Tuple[int, int]]`: base-bucket index (`ts // 1800`) → `(tx_count, amount_cents)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stats_service.py`:

```python
"""Pure tests for the stats series math (no stores)."""
import pytest

from app.services import stats_service as ss


def test_currency_code_strips_ibm_prefix_only():
    assert ss.currency_code("IBM_AML_US Dollar") == "US Dollar"
    assert ss.currency_code("CARD") == "CARD"


def test_period_window_anchors_to_dataset_end():
    start, end, bucket = ss.period_window("24h", 1000, 100000)
    assert (start, end, bucket) == (100000 - 86400, 100000, 1800)
    start, end, bucket = ss.period_window("7d", 1000, 100000 + 604800)
    assert (start, end, bucket) == (100000, 100000 + 604800, 21600)
    start, end, bucket = ss.period_window("all", 1000, 100000)
    assert (start, end, bucket) == (1000, 100000, 86400)
    with pytest.raises(ValueError):
        ss.period_window("90d", 0, 1)


def test_build_series_is_cumulative_with_uniform_baseline():
    # two base buckets (30 min each) inside a 1 h window bucketed at 1800 s
    start, end, bucket = 0, 3600, 1800
    hist = {0: (2, 10_000), 1: (3, 20_000)}          # ts 0..1799 and 1800..3599
    out = ss.build_series(hist, start, end, bucket)
    assert out["t"] == [0, 1800, 3600]
    assert out["volume"] == [0.0, 100.0, 300.0]       # cents → major units, cumulative
    assert out["txns"] == [0, 2, 5]
    assert out["baseline"] == [0.0, 150.0, 300.0]     # straight line to the final total


def test_build_series_ignores_buckets_outside_window():
    hist = {0: (1, 100), 10: (1, 100), 999: (1, 100)}
    out = ss.build_series(hist, start_ts=0, end_ts=3600, bucket_seconds=1800)
    assert out["txns"][-1] == 1 and out["volume"][-1] == 1.0


def test_build_series_single_point_window():
    out = ss.build_series({}, start_ts=5, end_ts=5, bucket_seconds=1800)
    assert out["t"] == [5] and out["volume"] == [0.0] and out["baseline"] == [0.0]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_stats_service.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.stats_service'`.

- [ ] **Step 3: Implement the pure part**

`app/services/stats_service.py`:

```python
"""Whole-graph statistics for the dashboard (spec §6.1–6.3).

A single Cypher pass over TRANSFER builds a (rail, 30-minute bucket) histogram;
every currency/period series derives from it in memory. Everything here that
does arithmetic is a pure function so it can be unit-tested without stores.
"""
import asyncio
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("stats")

BUCKET_BASE = 1800   # seconds; the histogram's resolution

# period → (window length in seconds or None for the whole dataset, bucket seconds)
PERIODS: Dict[str, Tuple[Optional[int], int]] = {
    "24h": (86400, 1800),
    "7d": (604800, 21600),
    "all": (None, 86400),
}

_IBM_PREFIX = "IBM_AML_"


def currency_code(rail: str) -> str:
    """Display code for a rail. IBM AML rails are `IBM_AML_<currency>`; any
    other rail string (CARD, WIRE, …) is shown verbatim."""
    if rail.startswith(_IBM_PREFIX):
        return rail[len(_IBM_PREFIX):]
    return rail


def period_window(period: str, dataset_start: int, dataset_end: int) -> Tuple[int, int, int]:
    """(start_ts, end_ts, bucket_seconds) for a period, anchored to the dataset's
    last timestamp — never wall-clock, the data is historical."""
    if period not in PERIODS:
        raise ValueError("period must be one of 24h, 7d, all")
    length, bucket = PERIODS[period]
    start = dataset_start if length is None else max(dataset_start, dataset_end - length)
    return start, dataset_end, bucket


def build_series(hist: Dict[int, Tuple[int, int]], start_ts: int, end_ts: int,
                 bucket_seconds: int) -> Dict[str, List[Any]]:
    """Cumulative volume/txn series over [start_ts, end_ts] plus a uniform-pace
    baseline. Point i sits at start_ts + i*bucket and holds everything that
    happened before it; point 0 is always zero."""
    span = max(0, end_ts - start_ts)
    n = int(math.ceil(span / bucket_seconds)) + 1 if span > 0 else 1
    t = [start_ts + i * bucket_seconds for i in range(n)]
    vol = [0.0] * n
    cnt = [0] * n
    for b, (count, amount_cents) in hist.items():
        ts = b * BUCKET_BASE
        if ts < start_ts or ts >= end_ts:
            continue
        i = min(n - 1, int((ts - start_ts) // bucket_seconds) + 1)
        vol[i] += amount_cents / 100.0
        cnt[i] += count
    for i in range(1, n):
        vol[i] += vol[i - 1]
        cnt[i] += cnt[i - 1]
    total = vol[-1]
    baseline = [total * i / (n - 1) for i in range(n)] if n > 1 else [0.0]
    return {"t": t, "volume": vol, "txns": cnt, "baseline": baseline}
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_stats_service.py -q -p no:cacheprovider`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/services/stats_service.py tests/test_stats_service.py
git commit -m "feat(stats): pure series math — anchored periods, cumulative volume, uniform baseline" -m "$TRAILER"
```

---

### Task 7: `StatsCache`, the stats endpoints, startup warm-up

**Files:**
- Modify: `app/services/stats_service.py` (append the cache)
- Modify: `app/api/endpoints.py` (two routes)
- Modify: `app/api/main.py` (warm-up task in lifespan)
- Modify: `app/viz/runner.py:130-134` (`_aggregate` invalidates)
- Test: `tests/test_stats_service.py` (append), `tests/test_stats_api.py` (new)

**Interfaces:**
- Produces (module `stats_service`): `cache: StatsCache`, `async warmup(session, pg)`, `invalidate()`.
- `StatsCache`: `ready() -> bool`, `overview() -> Dict`, `series(currency, period) -> Dict` (raises `KeyError` for an unknown currency, `ValueError` for a bad period), `is_flagged(account_id) -> bool`, `rail_for(code) -> Optional[str]`, `dataset_end_ts() -> Optional[int]`, `invalidate()`, `async build(session, pg)`.
- `session` is a zero-arg callable returning an async session context (the `app.viz.store` convention — pass `neo4j_client.driver.session`).

- [ ] **Step 1: Append failing tests for the cache**

Append to `tests/test_stats_service.py`:

```python
# ---- StatsCache with fake stores --------------------------------------------
import asyncio
from unittest.mock import AsyncMock, MagicMock


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    async def single(self):
        return self._rows[0] if self._rows else None

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


class _FakeSession:
    """Answers each Cypher query by a substring match on the query text."""
    def __init__(self, answers):
        self.answers = answers

    async def run(self, query, **params):
        for needle, rows in self.answers:
            if needle in query:
                return _FakeResult(rows)
        raise AssertionError("unexpected query: " + query)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_session(answers):
    return lambda: _FakeSession(answers)


def _fake_pg():
    return MagicMock(
        get_app_meta=AsyncMock(return_value={"name": "T", "source": "ibm-hi-small", "labelled": True,
                                             "start_ts": 0, "end_ts": 7200}),
        count_open_flags_by_type=AsyncMock(return_value={"AGGREGATE": 2, "CYCLE": 1}),
        get_account_ids_for_flag_type=AsyncMock(return_value=["f1", "f2"]),
        get_latest_pipeline_run=AsyncMock(return_value={"id": "r1", "status": "completed"}),
    )


def _answers():
    return [
        ("toInteger(t.ts / 1800)", [
            {"rail": "IBM_AML_US Dollar", "b": 0, "n": 2, "total": 10_000},
            {"rail": "IBM_AML_US Dollar", "b": 1, "n": 3, "total": 20_000},
            {"rail": "IBM_AML_Euro", "b": 2, "n": 1, "total": 5_000},
        ]),
        ("MATCH (a:Account) WHERE a.gnn_risk_score IS NOT NULL", [{"n": 9}]),
        ("count(DISTINCT a.community_id)", [{"n": 4}]),
        ("a.in_cycle = true", [{"n": 1}]),
        ("MATCH ()-[t:TRANSFER]->() RETURN count(t)", [{"n": 6}]),
        ("MATCH ()-[r:FLOWS_TO]->() RETURN count(r)", [{"n": 5}]),
        ("MATCH (a:Account) RETURN count(a)", [{"n": 10}]),
    ]


def test_cache_builds_overview_and_series():
    c = ss.StatsCache()
    assert not c.ready()
    asyncio.run(c.build(_fake_session(_answers()), _fake_pg()))
    assert c.ready()
    ov = c.overview()
    assert ov["accounts"] == 10 and ov["flows"] == 5 and ov["transactions"] == 6
    assert ov["scored_accounts"] == 9 and ov["communities"] == 4 and ov["in_cycle"] == 1
    assert ov["open_flags"] == {"AGGREGATE": 2, "CYCLE": 1, "total": 3}
    usd = next(x for x in ov["currencies"] if x["code"] == "US Dollar")
    assert usd["tx_count"] == 5 and usd["volume"] == 300.0 and usd["rail"] == "IBM_AML_US Dollar"
    assert ov["currencies"][0]["code"] == "US Dollar"          # sorted by tx_count desc
    assert ov["dataset"]["end_ts"] == 7200 and ov["latest_run"]["id"] == "r1"
    s = c.series("US Dollar", "all")
    assert s["currency"] == "US Dollar" and s["anchor_ts"] == 7200 and s["start_ts"] == 0
    assert s["volume"][-1] == 300.0 and s["txns"][-1] == 5
    assert c.is_flagged("f1") and not c.is_flagged("zzz")
    assert c.rail_for("Euro") == "IBM_AML_Euro" and c.rail_for("Nope") is None
    assert c.dataset_end_ts() == 7200


def test_cache_series_errors_and_invalidate():
    c = ss.StatsCache()
    asyncio.run(c.build(_fake_session(_answers()), _fake_pg()))
    with pytest.raises(KeyError):
        c.series("Nope", "7d")
    with pytest.raises(ValueError):
        c.series("Euro", "90d")
    c.invalidate()
    assert not c.ready()


def test_cache_dataset_bounds_fall_back_to_histogram():
    pg = _fake_pg()
    pg.get_app_meta = AsyncMock(return_value=None)
    c = ss.StatsCache()
    asyncio.run(c.build(_fake_session(_answers()), pg))
    ds = c.overview()["dataset"]
    assert ds["start_ts"] == 0 and ds["end_ts"] == 3 * 1800 and ds["labelled"] is False


def test_warmup_skips_without_pg():
    asyncio.run(ss.warmup(_fake_session([]), None))   # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_stats_service.py -q -p no:cacheprovider`
Expected: 4 new failures with `AttributeError: module 'app.services.stats_service' has no attribute 'StatsCache'`.

- [ ] **Step 3: Implement the cache**

Append to `app/services/stats_service.py`:

```python
_HIST_Q = (
    "MATCH ()-[t:TRANSFER]->() "
    "WITH t.rail AS rail, toInteger(t.ts / 1800) AS b, t.amount_cents AS amt "
    "RETURN rail, b, count(*) AS n, sum(amt) AS total"
)
_COUNT_QS = {
    "accounts": "MATCH (a:Account) RETURN count(a) AS n",
    "flows": "MATCH ()-[r:FLOWS_TO]->() RETURN count(r) AS n",
    "transactions": "MATCH ()-[t:TRANSFER]->() RETURN count(t) AS n",
    "scored_accounts": "MATCH (a:Account) WHERE a.gnn_risk_score IS NOT NULL RETURN count(a) AS n",
    "communities": "MATCH (a:Account) WHERE a.community_id IS NOT NULL RETURN count(DISTINCT a.community_id) AS n",
    "in_cycle": "MATCH (a:Account) WHERE a.in_cycle = true RETURN count(a) AS n",
}


class StatsCache:
    """Whole-graph numbers, computed once. `build` is the only expensive call
    (one ~8 s TRANSFER scan on the IBM graph); everything else is in-memory."""

    def __init__(self) -> None:
        self._hist: Dict[str, Dict[int, Tuple[int, int]]] = {}   # rail → base histogram
        self._counts: Dict[str, int] = {}
        self._open_flags: Dict[str, int] = {}
        self._flagged: Set[str] = set()
        self._dataset: Dict[str, Any] = {}
        self._latest_run: Optional[Dict[str, Any]] = None
        self._computed_at: Optional[int] = None
        self._lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------------
    def ready(self) -> bool:
        return self._computed_at is not None

    def invalidate(self) -> None:
        self._computed_at = None

    async def build(self, session: Callable[[], Any], pg: Any) -> None:
        async with self._lock:
            hist: Dict[str, Dict[int, Tuple[int, int]]] = {}
            counts: Dict[str, int] = {}
            async with session() as s:
                res = await s.run(_HIST_Q)
                async for r in res:
                    rail = r["rail"] or "UNKNOWN"
                    hist.setdefault(rail, {})[int(r["b"])] = (int(r["n"]), int(r["total"] or 0))
                for key, q in _COUNT_QS.items():
                    rec = await (await s.run(q)).single()
                    counts[key] = int(rec["n"]) if rec else 0
            dataset = await pg.get_app_meta("dataset") or {}
            if "start_ts" not in dataset or "end_ts" not in dataset:
                bs = [b for h in hist.values() for b in h]
                dataset = dict(dataset)
                dataset.setdefault("name", "loaded graph")
                dataset.setdefault("source", "unknown")
                dataset.setdefault("labelled", False)
                dataset["start_ts"] = min(bs) * BUCKET_BASE if bs else 0
                dataset["end_ts"] = (max(bs) + 1) * BUCKET_BASE if bs else 0
            open_flags = await pg.count_open_flags_by_type()
            flagged = set(await pg.get_account_ids_for_flag_type("AGGREGATE", "open"))
            latest = await pg.get_latest_pipeline_run()
            self._hist, self._counts, self._dataset = hist, counts, dataset
            self._open_flags, self._flagged, self._latest_run = open_flags, flagged, latest
            self._computed_at = int(time.time())
            logger.info("stats cache built: %d rails, %d accounts", len(hist), counts.get("accounts", 0))

    # -- reads ------------------------------------------------------------------
    def _currencies(self) -> List[Dict[str, Any]]:
        out = []
        for rail, h in self._hist.items():
            n = sum(c for c, _ in h.values())
            amt = sum(a for _, a in h.values())
            out.append({"code": currency_code(rail), "rail": rail, "tx_count": n, "volume": amt / 100.0})
        out.sort(key=lambda x: x["tx_count"], reverse=True)
        return out

    def rail_for(self, code: str) -> Optional[str]:
        for rail in self._hist:
            if currency_code(rail) == code or rail == code:
                return rail
        return None

    def overview(self) -> Dict[str, Any]:
        flags = dict(self._open_flags)
        flags["total"] = sum(self._open_flags.values())
        return {
            "dataset": self._dataset,
            **self._counts,
            "open_flags": flags,
            "currencies": self._currencies(),
            "latest_run": self._latest_run,
            "computed_at": self._computed_at,
        }

    def series(self, currency: str, period: str) -> Dict[str, Any]:
        rail = self.rail_for(currency)
        if rail is None:
            raise KeyError(currency)
        start, end, bucket = period_window(period, int(self._dataset["start_ts"]), int(self._dataset["end_ts"]))
        out = build_series(self._hist[rail], start, end, bucket)
        out.update({"currency": currency_code(rail), "period": period, "anchor_ts": end,
                    "start_ts": start, "bucket_seconds": bucket})
        return out

    def is_flagged(self, account_id: str) -> bool:
        return account_id in self._flagged

    def dataset_end_ts(self) -> Optional[int]:
        if not self.ready():
            return None
        return int(self._dataset["end_ts"])


cache = StatsCache()


def invalidate() -> None:
    cache.invalidate()


async def warmup(session: Callable[[], Any], pg: Any) -> None:
    """Build the cache in the background at startup. Never raises — a failure
    leaves the cache not-ready and the endpoints answer 503 until a retry."""
    if pg is None:
        return
    try:
        await cache.build(session, pg)
    except Exception:  # noqa: BLE001 — logged, endpoints report warming
        logger.exception("stats cache warm-up failed")
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_stats_service.py -q -p no:cacheprovider`
Expected: 9 passed.

- [ ] **Step 5: Add the endpoints and the warm-up**

Append to `app/api/endpoints.py` (after the existing imports add `from app.services import stats_service` and `from fastapi import HTTPException`):

```python
@router.get("/stats/overview")
async def stats_overview():
    if not stats_service.cache.ready():
        raise HTTPException(status_code=503, detail="stats warming up")
    return stats_service.cache.overview()


@router.get("/stats/volume-series")
async def stats_volume_series(
    currency: str = Query(..., description="Currency code, e.g. 'US Dollar'"),
    period: str = Query("7d", pattern="^(24h|7d|all)$"),
):
    if not stats_service.cache.ready():
        raise HTTPException(status_code=503, detail="stats warming up")
    try:
        return stats_service.cache.series(currency, period)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown currency: {currency}")
```

In `app/api/main.py` change the lifespan to:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    neo4j_client.connect()
    await viz_deps.startup()
    # Whole-graph stats take one ~8 s scan — build them off the request path.
    warm = asyncio.create_task(
        stats_service.warmup(neo4j_client.driver.session, viz_deps.pg()))
    yield
    warm.cancel()
    await viz_deps.shutdown()
    await neo4j_client.close()
    await redis_pool.disconnect()
```

with `import asyncio` and `from app.services import stats_service` added to the imports.

In `app/viz/runner.py` `_aggregate`, after `threshold.invalidate()` add:

```python
        from app.services import stats_service
        stats_service.invalidate()               # open-flag counts and latest_run changed
```

- [ ] **Step 6: API tests**

`tests/test_stats_api.py`:

```python
"""Stats endpoints: warming → 503, ready → cache reads, unknown currency → 404."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_overview_503_while_warming(client):
    from app.services import stats_service
    stats_service.cache.invalidate()
    r = client.get("/api/stats/overview")
    assert r.status_code == 503 and "warming" in r.json()["detail"]


def test_overview_and_series_when_ready(client):
    from app.services import stats_service
    c = stats_service.cache
    c._hist = {"IBM_AML_US Dollar": {0: (2, 10_000)}}
    c._counts = {"accounts": 1, "flows": 1, "transactions": 2, "scored_accounts": 1,
                 "communities": 1, "in_cycle": 0}
    c._dataset = {"start_ts": 0, "end_ts": 3600, "labelled": True}
    c._open_flags, c._flagged, c._latest_run, c._computed_at = {"AGGREGATE": 1}, set(), None, 1
    try:
        r = client.get("/api/stats/overview")
        assert r.status_code == 200 and r.json()["open_flags"]["total"] == 1
        r = client.get("/api/stats/volume-series?currency=US%20Dollar&period=24h")
        assert r.status_code == 200 and r.json()["volume"][-1] == 100.0
        assert client.get("/api/stats/volume-series?currency=Nope").status_code == 404
        assert client.get("/api/stats/volume-series?currency=US%20Dollar&period=90d").status_code == 422
    finally:
        c.invalidate()
```

Run: `python3 -m pytest tests/test_stats_api.py tests/test_viz_api.py -q -p no:cacheprovider`
Expected: 11 passed (the lifespan's warm-up returns immediately because `viz_deps.pg()` is `None` under the patched startup).

- [ ] **Step 7: Live check**

Run (background, leave it running for later tasks):
```bash
POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
  python3 -m uvicorn app.api.main:app --port 8000 --log-level info
```
Then, after ~15 s: `curl -s localhost:8000/api/stats/overview | python3 -m json.tool | head -40`
Expected: `accounts` ≈ 513989 (after the purge), `transactions` 5044315, 15 currencies with `US Dollar` first, `dataset.end_ts` 1663517880.
`curl -s 'localhost:8000/api/stats/volume-series?currency=US%20Dollar&period=7d' | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['t']), d['volume'][-1])"` → `29 <positive number>`.

- [ ] **Step 8: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/services/stats_service.py app/api/endpoints.py app/api/main.py app/viz/runner.py tests/test_stats_service.py tests/test_stats_api.py
git commit -m "feat(stats): StatsCache warmed at startup; /api/stats/overview and /volume-series" -m "$TRAILER"
```

---

### Task 8: Alerts — list and status update

**Files:**
- Create: `app/services/alerts_service.py`
- Modify: `app/api/endpoints.py`
- Test: `tests/test_alerts_api.py`

**Interfaces:**
- Produces: `alerts_service.shape_alert(row: Dict) -> Dict`, `async list_alerts(pg, flag_type, min_level, status, limit, offset) -> Dict` (`{"total", "items"}`), `async set_status(pg, flag_id, status) -> Optional[Dict]`.
- Routes: `GET /api/alerts`, `PATCH /api/alerts/{id}/status`.

- [ ] **Step 1: Failing tests**

`tests/test_alerts_api.py`:

```python
"""Alerts: shaping, list with total, status update, validation."""
import asyncio
from datetime import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.services import alerts_service


def _row(**over):
    base = {"id": 7, "flag_type": "AGGREGATE", "fingerprint": "agg:x", "account_ids": ["x", "y"],
            "risk_level": "high", "risk_score": 0.81, "explanation": "why",
            "details": '{"signals": {"gnn": true}}', "status": "open",
            "first_detected_at": datetime(2026, 9, 1), "last_detected_at": datetime(2026, 9, 2),
            "detection_count": 3, "created_at": datetime(2026, 9, 1)}
    base.update(over)
    return base


def test_shape_alert_parses_details_and_primary_account():
    a = alerts_service.shape_alert(_row())
    assert a["primary_account"] == "x" and a["details"] == {"signals": {"gnn": True}}
    assert a["first_detected_at"].startswith("2026-09-01") and a["risk_score"] == 0.81
    assert alerts_service.shape_alert(_row(account_ids=[], details=None))["primary_account"] is None


def test_list_alerts_returns_total_and_items():
    pg = MagicMock(get_risk_flags=AsyncMock(return_value=[_row()]),
                   count_risk_flags=AsyncMock(return_value=42))
    out = asyncio.run(alerts_service.list_alerts(pg, "AGGREGATE", "medium", "open", 10, 20))
    assert out["total"] == 42 and out["items"][0]["id"] == 7
    assert pg.get_risk_flags.await_args.kwargs == {"flag_type": "AGGREGATE", "min_level": "medium",
                                                    "status": "open", "limit": 10, "offset": 20}


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_alerts_endpoint(client):
    from app.viz import deps
    pg = MagicMock(get_risk_flags=AsyncMock(return_value=[_row()]),
                   count_risk_flags=AsyncMock(return_value=1))
    with patch.object(deps, "pg", lambda: pg):
        r = client.get("/api/alerts?flag_type=AGGREGATE&min_level=high&limit=5")
    assert r.status_code == 200
    assert r.json()["total"] == 1 and r.json()["items"][0]["primary_account"] == "x"
    assert client.get("/api/alerts?limit=9999").status_code == 422
    assert client.get("/api/alerts?min_level=severe").status_code == 422


def test_alert_status_update(client):
    from app.viz import deps
    pg = MagicMock(update_risk_flag_status=AsyncMock(return_value=_row(status="reviewed")))
    with patch.object(deps, "pg", lambda: pg):
        r = client.patch("/api/alerts/7/status", json={"status": "reviewed"})
        assert r.status_code == 200 and r.json()["status"] == "reviewed"
        assert client.patch("/api/alerts/7/status", json={"status": "bogus"}).status_code == 422
    pg2 = MagicMock(update_risk_flag_status=AsyncMock(return_value=None))
    with patch.object(deps, "pg", lambda: pg2):
        assert client.patch("/api/alerts/999/status", json={"status": "dismissed"}).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_alerts_api.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.alerts_service'`.

- [ ] **Step 3: Implement**

`app/services/alerts_service.py`:

```python
"""Alerts = rows of Postgres risk_flags, shaped for the UI (spec §6.4–6.5)."""
import json
from datetime import datetime
from typing import Any, Dict, Optional


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v is not None else None)


def shape_alert(row: Dict[str, Any]) -> Dict[str, Any]:
    details = row.get("details") or {}
    if isinstance(details, str):
        details = json.loads(details)
    ids = list(row.get("account_ids") or [])
    return {
        "id": int(row["id"]),
        "flag_type": row["flag_type"],
        "risk_level": row["risk_level"],
        "risk_score": float(row["risk_score"]),
        "explanation": row["explanation"],
        "account_ids": ids,
        "primary_account": ids[0] if ids else None,
        "details": details,
        "status": row["status"],
        "first_detected_at": _iso(row.get("first_detected_at")),
        "last_detected_at": _iso(row.get("last_detected_at")),
        "detection_count": int(row.get("detection_count") or 1),
    }


async def list_alerts(pg: Any, flag_type: Optional[str], min_level: Optional[str],
                      status: Optional[str], limit: int, offset: int) -> Dict[str, Any]:
    rows = await pg.get_risk_flags(flag_type=flag_type, min_level=min_level, status=status,
                                   limit=limit, offset=offset)
    total = await pg.count_risk_flags(flag_type=flag_type, min_level=min_level, status=status)
    return {"total": int(total), "items": [shape_alert(r) for r in rows]}


async def set_status(pg: Any, flag_id: int, status: str) -> Optional[Dict[str, Any]]:
    row = await pg.update_risk_flag_status(flag_id, status)
    return shape_alert(row) if row else None
```

Append to `app/api/endpoints.py` (add `from app.viz import deps as viz_deps`, `from app.services import alerts_service`, and `from app.schemas.api import AlertPage, AlertOut, AlertStatusUpdate` to the imports):

```python
@router.get("/alerts", response_model=AlertPage)
async def list_alerts(
    flag_type: Optional[str] = Query(None, pattern="^[A-Z_]{2,20}$"),
    min_level: str = Query("low", pattern="^(low|medium|high|critical)$"),
    status: Optional[str] = Query("open", pattern="^(open|reviewed|dismissed|escalated)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await alerts_service.list_alerts(viz_deps.pg(), flag_type, min_level, status, limit, offset)


@router.patch("/alerts/{flag_id}/status", response_model=AlertOut)
async def update_alert_status(flag_id: int, body: AlertStatusUpdate):
    row = await alerts_service.set_status(viz_deps.pg(), flag_id, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="no such alert")
    return row
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_alerts_api.py -q -p no:cacheprovider`
Expected: 5 passed.

- [ ] **Step 5: Live check and commit**

With the uvicorn from Task 7 still running (it reloads only with `--reload`; restart it if needed):
`curl -s 'localhost:8000/api/alerts?limit=2&min_level=high' | python3 -m json.tool | head -30` → `total` in the thousands, two AGGREGATE items with non-empty `explanation`.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/services/alerts_service.py app/api/endpoints.py tests/test_alerts_api.py
git commit -m "feat(api): /api/alerts list with totals and PATCH status from risk_flags" -m "$TRAILER"
```

---

### Task 9: Transactions from `TRANSFER` edges

**Files:**
- Create: `app/services/transactions_service.py`
- Modify: `app/api/endpoints.py`
- Test: `tests/test_transactions_service.py`

**Interfaces:**
- Produces: `TIER_RANK`, `max_tier(a, b) -> str`, `shape_transaction(rec: Dict, is_flagged: Callable[[str], bool]) -> Dict`, `async list_latest(session, limit, rail) -> List[Dict]`, `async list_for_account(session, account_id, limit, rail) -> List[Dict]`.
- Route: `GET /api/transactions?limit=&account_id=&currency=`.

- [ ] **Step 1: Failing tests**

`tests/test_transactions_service.py`:

```python
"""Transactions: tier max, flagged join, Cypher parameterisation, endpoint."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.services import transactions_service as ts


def test_max_tier_and_defaults():
    assert ts.max_tier("low", "critical") == "critical"
    assert ts.max_tier(None, "medium") == "medium"
    assert ts.max_tier(None, None) == "low"


def test_shape_transaction():
    rec = {"txn_id": "t1", "sender_id": "s", "receiver_id": "r", "amount_cents": 1234,
           "rail": "IBM_AML_Euro", "event_type": "SETTLEMENT", "ts": 1661991600,
           "sender_tier": "low", "receiver_tier": "high"}
    out = ts.shape_transaction(rec, lambda a: a == "r")
    assert out["currency"] == "Euro" and out["risk_tier"] == "high" and out["flagged"] is True
    assert out["sender_tier"] == "low" and out["ts"] == 1661991600


class _Res:
    def __init__(self, rows): self.rows = rows
    def __aiter__(self):
        async def g():
            for r in self.rows: yield r
        return g()


class _Sess:
    def __init__(self, rows): self.rows, self.calls = rows, []
    async def run(self, q, **p):
        self.calls.append((q, p)); return _Res(self.rows)
    async def __aenter__(self): return self
    async def __aexit__(self, *e): return False


def test_list_latest_parameterises_rail_and_limit():
    rec = {"txn_id": "t1", "sender_id": "s", "receiver_id": "r", "amount_cents": 5, "rail": "CARD",
           "event_type": None, "ts": 10, "sender_tier": None, "receiver_tier": None}
    sess = _Sess([rec])
    rows = asyncio.run(ts.list_latest(lambda: sess, limit=7, rail="CARD"))
    q, p = sess.calls[0]
    assert "t.rail = $rail" in q and p == {"limit": 7, "rail": "CARD"} and "ORDER BY t.ts DESC" in q
    assert rows[0]["risk_tier"] == "low" and rows[0]["flagged"] is False
    sess2 = _Sess([])
    asyncio.run(ts.list_latest(lambda: sess2, limit=3, rail=None))
    assert "$rail" not in sess2.calls[0][0]


def test_list_for_account_uses_both_directions():
    sess = _Sess([])
    asyncio.run(ts.list_for_account(lambda: sess, "acc", limit=5, rail=None))
    q, p = sess.calls[0]
    assert "-[t:TRANSFER]-(" in q and "startNode(t)" in q and p == {"id": "acc", "limit": 5}


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_transactions_endpoint(client):
    canned = [{"txn_id": "t", "sender_id": "s", "receiver_id": "r", "amount_cents": 1, "currency": "CARD",
               "rail": "CARD", "event_type": None, "ts": 1, "sender_tier": "low", "receiver_tier": "low",
               "risk_tier": "low", "flagged": False}]
    with patch.object(ts, "list_latest", AsyncMock(return_value=canned)) as m:
        r = client.get("/api/transactions?limit=5")
    assert r.status_code == 200 and r.json()["items"][0]["txn_id"] == "t"
    assert m.await_args.kwargs["limit"] == 5
    with patch.object(ts, "list_for_account", AsyncMock(return_value=[])) as m2:
        r = client.get("/api/transactions?account_id=abc&limit=3")
    assert r.status_code == 200 and m2.await_args.args[1] == "abc"
    assert client.get("/api/transactions?limit=999").status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_transactions_service.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`app/services/transactions_service.py`:

```python
"""Per-transaction reads over Neo4j TRANSFER edges (spec §6.6).

Postgres has no transactions table for the bulk-loaded graph, so this is the
only source of transaction-level detail. `session` is the zero-arg session
factory convention used by app.viz.store.
"""
from typing import Any, Callable, Dict, List, Optional

from app.services.stats_service import currency_code

TIER_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_RETURN = (
    "RETURN t.txn_id AS txn_id, s.id AS sender_id, r.id AS receiver_id, "
    "t.amount_cents AS amount_cents, t.rail AS rail, t.event_type AS event_type, t.ts AS ts, "
    "s.gnn_risk_tier AS sender_tier, r.gnn_risk_tier AS receiver_tier "
    "ORDER BY t.ts DESC LIMIT $limit"
)


def max_tier(a: Optional[str], b: Optional[str]) -> str:
    ra, rb = TIER_RANK.get(a or "low", 0), TIER_RANK.get(b or "low", 0)
    return (a if ra >= rb else b) or "low"


def shape_transaction(rec: Dict[str, Any], is_flagged: Callable[[str], bool]) -> Dict[str, Any]:
    sender, receiver = rec["sender_id"], rec["receiver_id"]
    s_tier, r_tier = rec.get("sender_tier") or "low", rec.get("receiver_tier") or "low"
    return {
        "txn_id": str(rec["txn_id"]),
        "sender_id": sender,
        "receiver_id": receiver,
        "amount_cents": int(rec.get("amount_cents") or 0),
        "currency": currency_code(rec.get("rail") or ""),
        "rail": rec.get("rail") or "",
        "event_type": rec.get("event_type"),
        "ts": int(rec.get("ts") or 0),
        "sender_tier": s_tier,
        "receiver_tier": r_tier,
        "risk_tier": max_tier(s_tier, r_tier),
        "flagged": bool(is_flagged(sender) or is_flagged(receiver)),
    }


def _flag_lookup() -> Callable[[str], bool]:
    from app.services.stats_service import cache
    return cache.is_flagged if cache.ready() else (lambda _a: False)


async def list_latest(session: Callable[[], Any], limit: int, rail: Optional[str]) -> List[Dict[str, Any]]:
    if rail:
        query = "MATCH (s:Account)-[t:TRANSFER]->(r:Account) WHERE t.rail = $rail " + _RETURN
        params: Dict[str, Any] = {"limit": limit, "rail": rail}
    else:
        query = "MATCH (s:Account)-[t:TRANSFER]->(r:Account) " + _RETURN
        params = {"limit": limit}
    flagged = _flag_lookup()
    async with session() as s:
        res = await s.run(query, **params)
        return [shape_transaction(dict(r), flagged) async for r in res]


async def list_for_account(session: Callable[[], Any], account_id: str, limit: int,
                           rail: Optional[str]) -> List[Dict[str, Any]]:
    where = "WHERE t.rail = $rail " if rail else ""
    query = (
        "MATCH (a:Account {id: $id})-[t:TRANSFER]-(o:Account) " + where +
        "WITH t, startNode(t) AS s, endNode(t) AS r " + _RETURN
    )
    params: Dict[str, Any] = {"id": account_id, "limit": limit}
    if rail:
        params["rail"] = rail
    flagged = _flag_lookup()
    async with session() as s:
        res = await s.run(query, **params)
        return [shape_transaction(dict(r), flagged) async for r in res]
```

Append to `app/api/endpoints.py` (imports: `from app.services import transactions_service`, `from app.schemas.api import TransactionPage`, `from app.db.neo4j import neo4j_client`):

```python
def _rail_for(currency: Optional[str]) -> Optional[str]:
    if not currency:
        return None
    from app.services.stats_service import cache
    return (cache.rail_for(currency) if cache.ready() else None) or currency


@router.get("/transactions", response_model=TransactionPage)
async def list_transactions(
    limit: int = Query(50, ge=1, le=200),
    account_id: Optional[str] = Query(None, min_length=1, max_length=128),
    currency: Optional[str] = Query(None, max_length=64),
):
    rail = _rail_for(currency)
    session = neo4j_client.driver.session
    if account_id:
        items = await transactions_service.list_for_account(session, account_id, limit=limit, rail=rail)
    else:
        items = await transactions_service.list_latest(session, limit=limit, rail=rail)
    return {"items": items}
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_transactions_service.py -q -p no:cacheprovider`
Expected: 6 passed.

- [ ] **Step 5: Live check (index-backed latency) and commit**

`time curl -s 'localhost:8000/api/transactions?limit=5' | python3 -c "import sys,json; d=json.load(sys.stdin); print([ (x['currency'], x['ts']) for x in d['items']])"`
Expected: five rows with `ts` descending from 1663517880, well under 1 s once the `transfer_ts` index is `ONLINE` (`SHOW INDEXES YIELD name, state`). Then pick a sender id from the output and check `curl -s 'localhost:8000/api/transactions?account_id=<id>&limit=5'` returns rows.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/services/transactions_service.py app/api/endpoints.py tests/test_transactions_service.py
git commit -m "feat(api): /api/transactions from TRANSFER edges with tier and flagged join" -m "$TRAILER"
```

---

### Task 10: Graph service fixes and `/graph/account/{id}`

**Files:**
- Modify: `app/schemas/graph.py` (`NodeData`)
- Modify: `app/services/graph_service.py`
- Modify: `app/api/endpoints.py`
- Test: `tests/test_graph_service.py`

**Interfaces:**
- Produces: `GraphService.node_data(props: Dict) -> NodeData` (pure), `GraphService.get_account(account_id) -> Optional[NodeData]`; `NodeData` gains `gnn_risk_score: Optional[float]`, `gnn_risk_tier: Optional[str]`, `in_cycle: bool`, `marked: bool`.
- Route: `GET /api/graph/account/{account_id}`.

- [ ] **Step 1: Failing tests**

`tests/test_graph_service.py`:

```python
"""Graph service: GNN-score coalescing, node fields, flow over TRANSFER, account route."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.services.graph_service import GraphService


def test_node_data_prefers_aggregated_then_gnn_score():
    n = GraphService.node_data({"id": "a", "gnn_risk_score": 0.9, "gnn_risk_tier": "critical", "in_cycle": True})
    assert n.risk_score == 0.9 and n.risk_tier == "critical" and n.in_cycle and n.marked
    n2 = GraphService.node_data({"id": "b", "risk_score": 0.3, "gnn_risk_score": 0.9})
    assert n2.risk_score == 0.3 and n2.gnn_risk_score == 0.9       # aggregator verdict wins for display
    n3 = GraphService.node_data({"id": "c"})
    assert n3.risk_score == 0.0 and n3.risk_tier == "low" and not n3.marked
    assert n3.label == "c" and "id" not in n3.attributes


class _Res:
    def __init__(self, row): self.row = row
    async def single(self): return self.row


class _Sess:
    def __init__(self, row): self.row, self.calls = row, []
    async def run(self, q, **p):
        self.calls.append((q, p)); return _Res(self.row)
    async def __aenter__(self): return self
    async def __aexit__(self, *e): return False


def test_flow_between_reads_transfer_edges_anchored_to_dataset():
    sess = _Sess({"n": 3, "total": 300})
    from app.services import stats_service
    with patch.object(stats_service.cache, "dataset_end_ts", lambda: 1_000_000), \
         patch("app.services.graph_service.neo4j_client") as nc:
        nc.driver.session = lambda: sess
        out = asyncio.run(GraphService.get_flow_between("a", "b", "24h"))
    q, p = sess.calls[0]
    assert "[t:TRANSFER]->" in q and p == {"a": "a", "b": "b", "min_ts": 1_000_000 - 86400}
    assert out["tx_count"] == 3 and out["total_volume_cents"] == 300.0 and out["avg_amount_cents"] == 100.0


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_account_route(client):
    node = GraphService.node_data({"id": "a", "gnn_risk_score": 0.7, "gnn_risk_tier": "high"})
    with patch.object(GraphService, "get_account", AsyncMock(return_value=node)):
        r = client.get("/api/graph/account/a")
    assert r.status_code == 200 and r.json()["gnn_risk_tier"] == "high"
    with patch.object(GraphService, "get_account", AsyncMock(return_value=None)):
        assert client.get("/api/graph/account/zzz").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_graph_service.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: type object 'GraphService' has no attribute 'node_data'`.

- [ ] **Step 3: Extend `NodeData`**

In `app/schemas/graph.py` replace the `NodeData` class with:

```python
class NodeData(BaseModel):
    id: str
    label: str
    node_type: str = "account"
    risk_score: float = 0.0          # aggregator verdict if one was written, else the GNN score
    risk_tier: str = "low"           # low, medium, high, critical
    gnn_risk_score: Optional[float] = None
    gnn_risk_tier: Optional[str] = None
    in_cycle: bool = False
    marked: bool = False             # our pipeline's mark (cycle OR GNN ≥ tuned cutoff)
    community_id: Optional[str] = None
    pagerank_score: Optional[float] = 0.0
    attributes: Dict[str, Any] = Field(default_factory=dict)
```

(`community_id` becomes `Optional[str]` — the stored ids are 12-hex strings such as `509aa8101d76`, not ints.)

- [ ] **Step 4: Rewrite the node shaping and the flow query**

In `app/services/graph_service.py`:

Replace the imports and the `_map_risk_tier` helper at the top with:

```python
import time
from typing import Dict, Any, List, Optional

from neo4j.exceptions import ClientError

from app.db.neo4j import neo4j_client
from app.schemas.graph import GraphElements, NodeElement, NodeData, EdgeElement, EdgeData

_HIDDEN = {"id", "risk_score", "risk_tier", "gnn_risk_score", "gnn_risk_tier", "in_cycle",
           "community_id", "pagerank_score", "label", "node_type"}


class GraphService:
    @staticmethod
    def _map_risk_tier(score: float) -> str:
        if score >= 0.85: return "critical"
        if score >= 0.65: return "high"
        if score >= 0.40: return "medium"
        return "low"

    @classmethod
    def node_data(cls, props: Dict[str, Any]) -> NodeData:
        """One rule for turning stored Account properties into API node data.
        Real accounts carry gnn_* properties; risk_score/risk_tier exist only
        once the aggregator has written a verdict, and take precedence then."""
        from app.viz import threshold
        nid = str(props.get("id"))
        gnn = props.get("gnn_risk_score")
        gnn = float(gnn) if gnn is not None else None
        score = props.get("risk_score")
        score = float(score) if score is not None else (gnn if gnn is not None else 0.0)
        tier = props.get("risk_tier") or props.get("gnn_risk_tier") or cls._map_risk_tier(score)
        in_cycle = bool(props.get("in_cycle", False))
        return NodeData(
            id=nid,
            label=str(props.get("label") or nid[:8]),
            node_type=str(props.get("node_type") or "account"),
            risk_score=score,
            risk_tier=str(tier),
            gnn_risk_score=gnn,
            gnn_risk_tier=props.get("gnn_risk_tier"),
            in_cycle=in_cycle,
            marked=threshold.is_marked(gnn, in_cycle, threshold.model_threshold()),
            community_id=(str(props["community_id"]) if props.get("community_id") is not None else None),
            pagerank_score=float(props.get("pagerank_score") or 0.0),
            attributes={k: v for k, v in props.items() if k not in _HIDDEN},
        )

    @classmethod
    async def get_account(cls, account_id: str) -> Optional[NodeData]:
        async with neo4j_client.driver.session() as session:
            rec = await (await session.run(
                "MATCH (a:Account {id: $id}) RETURN a", id=account_id)).single()
        return cls.node_data(dict(rec["a"])) if rec else None
```

Then in `get_subgraph`, replace the node loop body (from `score = float(props.get("risk_score", 0.0))` through the closing `))` of `nodes_out.append(...)`) with:

```python
                nodes_out.append(NodeElement(data=cls.node_data(props)))
```

In `get_shortest_path`, replace the `nodes_out = [...]` list comprehension with:

```python
            nodes_out = [NodeElement(data=cls.node_data(dict(n))) for n in record["nodes"]]
```

Replace the whole `get_flow_between` with:

```python
    @staticmethod
    async def get_flow_between(account_a: str, account_b: str, window: str = "7d") -> Dict[str, Any]:
        """Volume a→b inside a window, read from TRANSFER edges. The window is
        anchored to the loaded dataset's last timestamp (the data is historical);
        wall-clock only when no stats are loaded."""
        from app.services.stats_service import cache
        seconds_map = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
        anchor = cache.dataset_end_ts() or int(time.time())
        min_ts = anchor - seconds_map.get(window, 604800)
        query = (
            "MATCH (a:Account {id: $a})-[t:TRANSFER]->(b:Account {id: $b}) "
            "WHERE t.ts >= $min_ts "
            "RETURN count(t) AS n, coalesce(sum(t.amount_cents), 0) AS total"
        )
        async with neo4j_client.driver.session() as session:
            rec = await (await session.run(query, a=account_a, b=account_b, min_ts=min_ts)).single()
        n = int(rec["n"]) if rec else 0
        total = float(rec["total"]) if rec else 0.0
        return {
            "source": account_a, "target": account_b, "window": window,
            "total_volume_cents": total, "tx_count": n,
            "avg_amount_cents": (total / n) if n else 0.0,
            "path_count": 1 if n else 0,
        }
```

Remove the now-unused `from app.db.redis import get_redis` import.

- [ ] **Step 5: Add the route**

Append to `app/api/endpoints.py` (import `NodeData` from `app.schemas.graph`):

```python
@router.get("/graph/account/{account_id}", response_model=NodeData)
async def get_account(account_id: str):
    node = await GraphService.get_account(account_id)
    if node is None:
        raise HTTPException(status_code=404, detail="no such account")
    return node
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_graph_service.py -q -p no:cacheprovider` — Expected: 3 passed.
Run: `python3 -m pytest -q -p no:cacheprovider` — Expected: all green (the store/viz tests do not exercise `GraphService`).

- [ ] **Step 7: Live check and commit**

Take an account id from `/api/alerts?limit=1` (`primary_account`) and run
`curl -s "localhost:8000/api/graph/subgraph?account_id=<id>&depth=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print([(n['data']['risk_tier'], n['data']['gnn_risk_score']) for n in d['nodes']][:5])"`
Expected: tiers other than `low` appear and `gnn_risk_score` is populated (before this task every node was `low`).

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/schemas/graph.py app/services/graph_service.py app/api/endpoints.py tests/test_graph_service.py
git commit -m "fix(graph): surface GNN scores/tiers on nodes, flow over TRANSFER, account lookup" -m "Real accounts carry gnn_risk_score, not risk_score, so every node rendered low." -m "$TRAILER"
```

---

### Task 11: Pipeline aliases under `/api/pipeline` and the metrics curve

**Files:**
- Modify: `app/viz/router.py`
- Modify: `app/api/main.py`
- Test: `tests/test_pipeline_alias.py`

**Interfaces:**
- Produces: `app.viz.router.pages`, `app.viz.router.api`, `app.viz.router.router` (both combined — unchanged for existing imports); `GET /metrics/curve?points=` on the api sub-router; `/api/pipeline/*` serves everything `/viz/*` serves except the HTML index.

- [ ] **Step 1: Failing test**

`tests/test_pipeline_alias.py`:

```python
"""The viz JSON routes are also served under /api/pipeline; the HTML index is not."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_alias_serves_threshold_and_not_index(client):
    from app.viz import threshold
    with patch.object(threshold, "model_threshold", lambda: 0.74):
        a = client.get("/api/pipeline/threshold")
        b = client.get("/viz/threshold")
    assert a.status_code == 200 and a.json() == b.json() == {"default": 0.74, "min": 0.5, "max": 0.95}
    assert client.get("/api/pipeline/").status_code == 404


def test_metrics_curve(client):
    from app.viz import metrics
    def fake_conf(cutoff):
        return {"loaded": True, "cutoff": cutoff, "precision": 1 - cutoff, "recall": cutoff,
                "tp": 1, "fp": 2, "fn": 3, "tn": 4, "marked": 3, "total": 10}
    with patch.object(metrics, "ensure_loaded", AsyncMock()), \
         patch.object(metrics, "confusion_at", fake_conf):
        r = client.get("/api/pipeline/metrics/curve?points=4")
    d = r.json()
    assert r.status_code == 200 and d["loaded"] is True
    assert d["cutoffs"] == [0.5, 0.65, 0.8, 0.95] and d["recall"] == d["cutoffs"]
    assert len(d["precision"]) == len(d["fp"]) == len(d["tp"]) == len(d["marked"]) == 4
    with patch.object(metrics, "ensure_loaded", AsyncMock()), \
         patch.object(metrics, "confusion_at", lambda c: {"loaded": False}):
        assert client.get("/api/pipeline/metrics/curve").json() == {"loaded": False}
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_pipeline_alias.py -q -p no:cacheprovider`
Expected: FAIL — `/api/pipeline/threshold` returns 404.

- [ ] **Step 3: Split the router**

In `app/viz/router.py`, change the router declarations at the top to:

```python
pages = APIRouter()   # the server-rendered viewer page — mounted at /viz only
api = APIRouter()     # JSON routes — mounted at /viz and again at /api/pipeline
router = APIRouter()  # combined; existing imports and tests use this name
```

Change `@router.get("/")` (the index) to `@pages.get("/")`, and every other `@router.get/post(...)` decorator in the file to `@api.get/post(...)`. Then, at the bottom of the file, add:

```python
@api.get("/metrics/curve")
async def metrics_curve(points: int = Query(46, ge=2, le=200)):
    """Precision/recall/counts at evenly spaced cutoffs — lets a client draw the
    curve and interpolate slider positions without a request per drag (and lets
    the static demo bake it)."""
    await metrics.ensure_loaded(_session())
    lo, hi = threshold.MIN_CUTOFF, threshold.MAX_CUTOFF
    cutoffs = [round(lo + (hi - lo) * i / (points - 1), 4) for i in range(points)]
    rows = [metrics.confusion_at(c) for c in cutoffs]
    if not rows or not rows[0].get("loaded"):
        return {"loaded": False}
    return {
        "loaded": True,
        "cutoffs": cutoffs,
        "precision": [r["precision"] for r in rows],
        "recall": [r["recall"] for r in rows],
        "tp": [r["tp"] for r in rows],
        "fp": [r["fp"] for r in rows],
        "marked": [r["marked"] for r in rows],
    }


router.include_router(pages)
router.include_router(api)
```

In `app/api/main.py` change the viz imports/mounts to:

```python
from app.viz.router import router as viz_router, api as viz_api_router
...
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(viz_router, prefix="/viz")
app.include_router(viz_api_router, prefix="/api/pipeline")
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_pipeline_alias.py tests/test_viz_api.py -q -p no:cacheprovider`
Expected: 11 passed (existing viz tests still patch `app.viz.router.PipelineRunner` and hit `/viz/...`).

- [ ] **Step 5: Live check and commit**

`curl -s 'localhost:8000/api/pipeline/metrics/curve?points=5' | python3 -m json.tool` → `loaded: true` with 5 cutoffs (the first call loads 514k scores, ~10 s).

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/viz/router.py app/api/main.py tests/test_pipeline_alias.py
git commit -m "feat(api): serve the pipeline JSON under /api/pipeline; add /metrics/curve" -m "$TRAILER"
```

---

### Task 12: Explanation service (Ollama → Anthropic → rule-based)

**Files:**
- Create: `app/services/explanation_service.py`
- Modify: `app/services/ai_enrichment.py` (delegate)
- Modify: `app/core/config.py` (three settings)
- Modify: `app/api/endpoints.py` (`/enrich` response model, `/system/llm`)
- Modify: `app/api/main.py` (configure at startup)
- Test: `tests/test_explanation_service.py`

**Interfaces:**
- Produces (module `explanation_service`):
  - `AccountEvidence` dataclass; `build_evidence(node: Dict, payers: List[Dict], payees: List[Dict], flags: List[Dict], dataset: Dict) -> AccountEvidence` (pure)
  - `evidence_prompt(ev: AccountEvidence) -> str` (pure)
  - `rule_based(ev: AccountEvidence) -> ModelJSON` (pure)
  - `resolve_provider(provider_env: str, api_key: str, ollama_reachable: bool) -> str` (pure; returns `anthropic|ollama|none`)
  - `async check_ollama(base_url: str) -> bool`
  - classes `AnthropicProvider(model, api_key)`, `OllamaProvider(model, base_url)` with `async generate(ev) -> ModelJSON` and `.name`, `.model`
  - `ExplanationService(session_factory, pg_factory, provider=None)` with `async explain(account_id) -> ExplanationOut`, `async gather(account_id) -> Optional[AccountEvidence]`, `status() -> Dict`
  - module singleton `service` and `async configure(settings) -> None`
- Consumes: `ModelJSON`, `ExplanationOut`, `TYPOLOGIES` from `app.schemas.api`; `stats_service.cache` for dataset info.
- Routes: `GET /api/accounts/{id}/enrich` → `ExplanationOut`; `GET /api/system/llm` → `LLMStatus`.

- [ ] **Step 1: Failing tests**

`tests/test_explanation_service.py`:

```python
"""Explanation service: evidence, prompt, rule-based fallback, provider selection,
fallback on provider failure, and the two routes. No network anywhere."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.schemas.api import ModelJSON
from app.services import explanation_service as es


def _node(**over):
    base = {"id": "acct1", "gnn_risk_score": 0.83, "gnn_risk_tier": "high", "in_cycle": False,
            "community_id": "c9", "pagerank_score": 1e-5}
    base.update(over)
    return base


def test_build_evidence_and_prompt():
    ev = es.build_evidence(
        _node(), payers=[{"id": "p1", "amount": 5000.0, "n": 3, "tier": "low"}],
        payees=[{"id": "q1", "amount": 100.0, "n": 1, "tier": "critical"}],
        flags=[{"flag_type": "AGGREGATE", "risk_level": "high", "explanation": "Marked (GNN 0.83)."}],
        dataset={"name": "T", "labelled": True})
    assert ev.account_id == "acct1" and ev.gnn_risk_score == 0.83 and ev.payees[0]["tier"] == "critical"
    p = es.evidence_prompt(ev)
    assert "acct1" in p and "0.83" in p and "AGGREGATE" in p and "q1" in p
    assert "typology" not in p.lower().split("evidence")[0]   # no label hints in the header


def test_rule_based_tiers_and_typology_hint():
    hi = es.rule_based(es.build_evidence(_node(gnn_risk_score=0.9, in_cycle=True), [], [], [], {}))
    assert hi.risk_level == "critical" and hi.detected_typology == "CYCLE" and hi.confidence >= 0.9
    lo = es.rule_based(es.build_evidence(_node(gnn_risk_score=0.1, gnn_risk_tier=None), [], [], [], {}))
    assert lo.risk_level == "low" and lo.detected_typology is None
    fan = es.rule_based(es.build_evidence(
        _node(gnn_risk_score=0.7, gnn_risk_tier="high"), [], [{"id": str(i), "amount": 1.0, "n": 1, "tier": "low"} for i in range(6)], [], {}))
    assert fan.detected_typology == "FAN-OUT"


def test_resolve_provider_precedence():
    assert es.resolve_provider("ollama", "sk", True) == "ollama"        # explicit wins
    assert es.resolve_provider("", "sk", True) == "anthropic"           # key beats reachable ollama
    assert es.resolve_provider("", "", True) == "ollama"
    assert es.resolve_provider("", "", False) == "none"
    assert es.resolve_provider("bogus", "", False) == "none"


class _Provider:
    name, model = "fake", "fake-1"
    def __init__(self, result=None, exc=None): self.result, self.exc = result, exc
    async def generate(self, ev):
        if self.exc: raise self.exc
        return self.result


def _service(provider):
    svc = es.ExplanationService(lambda: None, lambda: None, provider=provider)
    ev = es.build_evidence(_node(), [], [], [], {"name": "T"})
    svc.gather = AsyncMock(return_value=ev)
    return svc


def test_explain_uses_provider_and_adds_provenance():
    good = ModelJSON(risk_level="high", confidence=0.7, explanation="fan-in hub", detected_typology="FAN-IN",
                     compliance_summary="review")
    out = asyncio.run(_service(_Provider(result=good)).explain("acct1"))
    assert out.provider == "fake" and out.model == "fake-1" and out.explanation == "fan-in hub"
    assert out.account_id == "acct1" and out.generated_at > 0


def test_explain_falls_back_when_provider_fails():
    out = asyncio.run(_service(_Provider(exc=RuntimeError("boom"))).explain("acct1"))
    assert out.provider == "none" and out.risk_level == "high" and out.explanation


def test_explain_unknown_account_is_rule_based_low():
    svc = es.ExplanationService(lambda: None, lambda: None, provider=_Provider())
    svc.gather = AsyncMock(return_value=None)
    out = asyncio.run(svc.explain("nope"))
    assert out.provider == "none" and out.risk_level == "low"


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()), \
         patch.object(es, "configure", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_enrich_and_llm_status_routes(client):
    good = ModelJSON(risk_level="medium", confidence=0.5, explanation="e", compliance_summary="c")
    svc = _service(_Provider(result=good))
    with patch.object(es, "service", svc):
        r = client.get("/api/accounts/acct1/enrich")
        assert r.status_code == 200 and r.json()["provider"] == "fake" and r.json()["risk_level"] == "medium"
        s = client.get("/api/system/llm")
        assert s.status_code == 200 and s.json() == {"provider": "fake", "model": "fake-1", "reachable": True}
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_explanation_service.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.explanation_service'`.

- [ ] **Step 3: Settings**

In `app/core/config.py` after `ANTHROPIC_API_KEY: str = ""` add:

```python
    # --- Explanation service (app/services/explanation_service.py) ---
    # LLM_PROVIDER: "anthropic" | "ollama" | "none" | "" (auto: key → anthropic,
    # else a reachable Ollama, else none). LLM_MODEL defaults per provider.
    LLM_PROVIDER: str = ""
    LLM_MODEL: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"
```

- [ ] **Step 4: Implement the service**

`app/services/explanation_service.py`:

```python
"""Natural-language risk explanations (spec §7).

Evidence about an account is gathered from the stores (pure `build_evidence`
turns raw rows into an `AccountEvidence`), then a provider turns it into a
schema-validated `ModelJSON`:

    ollama     — a local model via the Ollama HTTP API (free, default when reachable)
    anthropic  — the Claude API (opt-in: needs ANTHROPIC_API_KEY)
    none       — deterministic text from the signals; also the fallback whenever a
                 provider errors or returns something off-schema

Nothing here ever raises to the caller for lack of a model.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pydantic import ValidationError

from app.schemas.api import ExplanationOut, ModelJSON, TYPOLOGIES

logger = logging.getLogger("explain")

DEFAULT_MODELS = {"anthropic": "claude-opus-5", "ollama": "llama3.2"}

SYSTEM_PROMPT = (
    "You are a financial-crime analyst writing a short, plain-English risk explanation "
    "for one account in a payment network, for a compliance reviewer. Use ONLY the "
    "evidence provided. Do not invent transactions, amounts, parties or facts. If the "
    "evidence is weak, say so and lower your confidence. Money-laundering typologies "
    "you may name: " + ", ".join(TYPOLOGIES) + ". Respond with a JSON object with exactly "
    "these keys: risk_level (low|medium|high|critical), confidence (0-1), explanation "
    "(<=120 words), detected_typology (one of the typologies or null), "
    "compliance_summary (one sentence for the audit log)."
)


# --- evidence ------------------------------------------------------------------

@dataclass
class AccountEvidence:
    account_id: str
    gnn_risk_score: Optional[float]
    gnn_risk_tier: Optional[str]
    in_cycle: bool
    community_id: Optional[str]
    pagerank_score: float
    payers: List[Dict[str, Any]] = field(default_factory=list)   # {id, amount, n, tier}
    payees: List[Dict[str, Any]] = field(default_factory=list)
    flags: List[Dict[str, Any]] = field(default_factory=list)    # {flag_type, risk_level, explanation}
    dataset: Dict[str, Any] = field(default_factory=dict)


def build_evidence(node: Dict[str, Any], payers: List[Dict[str, Any]], payees: List[Dict[str, Any]],
                   flags: List[Dict[str, Any]], dataset: Dict[str, Any]) -> AccountEvidence:
    gnn = node.get("gnn_risk_score")
    return AccountEvidence(
        account_id=str(node.get("id")),
        gnn_risk_score=float(gnn) if gnn is not None else None,
        gnn_risk_tier=node.get("gnn_risk_tier"),
        in_cycle=bool(node.get("in_cycle", False)),
        community_id=(str(node["community_id"]) if node.get("community_id") is not None else None),
        pagerank_score=float(node.get("pagerank_score") or 0.0),
        payers=[dict(p) for p in payers],
        payees=[dict(p) for p in payees],
        flags=[{"flag_type": f.get("flag_type"), "risk_level": f.get("risk_level"),
                "explanation": f.get("explanation")} for f in flags],
        dataset={"name": dataset.get("name"), "labelled": bool(dataset.get("labelled", False))},
    )


def evidence_prompt(ev: AccountEvidence) -> str:
    """The user turn. Deliberately carries no ground-truth label or typology —
    the model must reason from structure and signals only."""
    body = {
        "account_id": ev.account_id,
        "gnn_risk_score": ev.gnn_risk_score,
        "gnn_risk_tier": ev.gnn_risk_tier,
        "on_detected_cycle": ev.in_cycle,
        "community_id": ev.community_id,
        "pagerank_score": ev.pagerank_score,
        "top_payers": ev.payers,
        "top_payees": ev.payees,
        "open_flags": ev.flags,
        "dataset": ev.dataset.get("name"),
    }
    return "Evidence:\n" + json.dumps(body, indent=1, sort_keys=True) + "\nWrite the explanation JSON."


# --- rule-based provider (also the fallback) -------------------------------------

def _tier_from_score(score: float) -> str:
    if score >= 0.85: return "critical"
    if score >= 0.65: return "high"
    if score >= 0.40: return "medium"
    return "low"


def rule_based(ev: AccountEvidence) -> ModelJSON:
    score = ev.gnn_risk_score or 0.0
    level = ev.gnn_risk_tier or _tier_from_score(score)
    typology: Optional[str] = None
    parts: List[str] = []
    if ev.in_cycle:
        level, typology = "critical", "CYCLE"
        parts.append("The account sits on a detected circular flow of funds.")
    parts.append("The graph model scores it %.2f (%s risk)." % (score, level))
    n_in, n_out = len(ev.payers), len(ev.payees)
    if typology is None and n_out >= 5 and n_out > 2 * max(n_in, 1):
        typology = "FAN-OUT"
        parts.append("It disperses funds to %d counterparties." % n_out)
    elif typology is None and n_in >= 5 and n_in > 2 * max(n_out, 1):
        typology = "FAN-IN"
        parts.append("It collects funds from %d counterparties." % n_in)
    risky = [p["id"] for p in ev.payers + ev.payees if p.get("tier") in ("high", "critical")]
    if risky:
        parts.append("It transacts with %d elevated-risk counterpart%s." % (len(risky), "y" if len(risky) == 1 else "ies"))
    for f in ev.flags[:2]:
        if f.get("explanation"):
            parts.append(str(f["explanation"]))
    confidence = 0.95 if ev.in_cycle else min(0.9, 0.5 + abs(score - 0.45))
    return ModelJSON(
        risk_level=level if level in ("low", "medium", "high", "critical") else "low",
        confidence=round(confidence, 2),
        explanation=" ".join(parts)[:1200],
        detected_typology=typology,
        compliance_summary="Rule-based summary from GNN score, cycle membership and counterparty tiers.",
    )


# --- providers -------------------------------------------------------------------

def resolve_provider(provider_env: str, api_key: str, ollama_reachable: bool) -> str:
    p = (provider_env or "").strip().lower()
    if p in ("anthropic", "ollama", "none"):
        return p
    if api_key:
        return "anthropic"
    if ollama_reachable:
        return "ollama"
    return "none"


async def check_ollama(base_url: str, timeout: float = 1.0) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(base_url.rstrip("/") + "/api/tags")
            return r.status_code == 200
    except Exception:  # noqa: BLE001 — unreachable is a normal state
        return False


def _parse_model_json(text: str) -> ModelJSON:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}") + 1]
    return ModelJSON.model_validate_json(text)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str) -> None:
        import anthropic
        self.model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate(self, ev: AccountEvidence) -> ModelJSON:
        system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
        messages = [{"role": "user", "content": evidence_prompt(ev)}]
        parse = getattr(self._client.messages, "parse", None)
        if parse is not None:
            resp = await parse(model=self.model, max_tokens=1024, system=system,
                               messages=messages, output_format=ModelJSON)
            parsed = getattr(resp, "parsed_output", None)
            if parsed is not None:
                return parsed
        resp = await self._client.messages.create(model=self.model, max_tokens=1024,
                                                  system=system, messages=messages)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        return _parse_model_json(text)


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str, base_url: str) -> None:
        import ollama
        self.model = model
        self._client = ollama.AsyncClient(host=base_url)

    async def generate(self, ev: AccountEvidence) -> ModelJSON:
        resp = await self._client.chat(
            model=self.model,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": evidence_prompt(ev)}],
            format=ModelJSON.model_json_schema(),
            options={"temperature": 0.2},
        )
        content = resp["message"]["content"]
        return _parse_model_json(content)


# --- the service -------------------------------------------------------------------

_EVIDENCE_Q = (
    "MATCH (a:Account {id: $id}) "
    "CALL (a) { MATCH (p:Account)-[r:FLOWS_TO]->(a) WHERE p <> a "
    "  RETURN collect({id: p.id, amount: r.total_amount, n: r.tx_count, tier: p.gnn_risk_tier})[..5] AS payers "
    "  ORDER BY r.total_amount DESC } "
    "CALL (a) { MATCH (a)-[r:FLOWS_TO]->(q:Account) WHERE q <> a "
    "  RETURN collect({id: q.id, amount: r.total_amount, n: r.tx_count, tier: q.gnn_risk_tier})[..5] AS payees "
    "  ORDER BY r.total_amount DESC } "
    "RETURN a, payers, payees"
)


class ExplanationService:
    def __init__(self, session_factory: Callable[[], Any], pg_factory: Callable[[], Any],
                 provider: Optional[Any] = None) -> None:
        self._session_factory = session_factory
        self._pg_factory = pg_factory
        self.provider = provider          # object with .name, .model, async generate(ev)

    def status(self) -> Dict[str, Any]:
        if self.provider is None:
            return {"provider": "none", "model": None, "reachable": False}
        return {"provider": self.provider.name, "model": self.provider.model, "reachable": True}

    async def gather(self, account_id: str) -> Optional[AccountEvidence]:
        session = self._session_factory()
        async with session() as s:
            rec = await (await s.run(_EVIDENCE_Q, id=account_id)).single()
        if not rec:
            return None
        pg = self._pg_factory()
        flags: List[Dict[str, Any]] = []
        if pg is not None:
            rows = await pg.get_risk_flags(status="open", limit=200)
            flags = [r for r in rows if account_id in (r.get("account_ids") or [])][:5]
        from app.services.stats_service import cache
        dataset = cache.overview()["dataset"] if cache.ready() else {}
        return build_evidence(dict(rec["a"]), list(rec["payers"] or []), list(rec["payees"] or []),
                              flags, dataset)

    async def explain(self, account_id: str) -> ExplanationOut:
        ev = await self.gather(account_id)
        if ev is None:
            ev = AccountEvidence(account_id=account_id, gnn_risk_score=None, gnn_risk_tier=None,
                                 in_cycle=False, community_id=None, pagerank_score=0.0)
            return self._wrap(ev, rule_based(ev), "none", None)
        if self.provider is None:
            return self._wrap(ev, rule_based(ev), "none", None)
        try:
            result = await self.provider.generate(ev)
            if result.detected_typology not in TYPOLOGIES:
                result.detected_typology = None
            return self._wrap(ev, result, self.provider.name, self.provider.model)
        except (ValidationError, ValueError, Exception) as exc:  # noqa: BLE001
            logger.warning("explanation provider %s failed for %s: %s", self.provider.name, account_id, exc)
            return self._wrap(ev, rule_based(ev), "none", None)

    @staticmethod
    def _wrap(ev: AccountEvidence, result: ModelJSON, provider: str, model: Optional[str]) -> ExplanationOut:
        return ExplanationOut(account_id=ev.account_id, provider=provider, model=model,
                              generated_at=int(time.time()), **result.model_dump())


service = ExplanationService(lambda: None, lambda: None, provider=None)


async def configure(settings: Any) -> None:
    """Pick the provider once at startup and wire the store factories."""
    from app.db.neo4j import neo4j_client
    from app.viz import deps as viz_deps
    reachable = await check_ollama(settings.OLLAMA_BASE_URL)
    name = resolve_provider(settings.LLM_PROVIDER, settings.ANTHROPIC_API_KEY, reachable)
    model = settings.LLM_MODEL or DEFAULT_MODELS.get(name, "")
    provider: Optional[Any] = None
    try:
        if name == "anthropic":
            provider = AnthropicProvider(model, settings.ANTHROPIC_API_KEY)
        elif name == "ollama" and reachable:
            provider = OllamaProvider(model, settings.OLLAMA_BASE_URL)
    except Exception as exc:  # noqa: BLE001 — a missing SDK degrades to rule-based
        logger.warning("could not initialise %s provider (%s); using rule-based", name, exc)
        provider = None
    service._session_factory = lambda: neo4j_client.driver.session
    service._pg_factory = viz_deps.pg
    service.provider = provider
    logger.info("explanation provider: %s", service.status())
```

Rewrite `app/services/ai_enrichment.py`:

```python
# backend/app/services/ai_enrichment.py
"""Compatibility shim: the risk aggregator delegates low-confidence cases here.
The real work lives in app.services.explanation_service."""
from app.schemas.api import ExplanationOut


class AIEnrichmentService:
    @staticmethod
    async def generate_explanation(account_id: str) -> ExplanationOut:
        from app.services import explanation_service
        return await explanation_service.service.explain(account_id)
```

- [ ] **Step 5: Routes and startup**

In `app/api/endpoints.py`: change the enrich route to

```python
@router.get("/accounts/{account_id}/enrich", response_model=ExplanationOut)
async def enrich_account(account_id: str):
    return await explanation_service.service.explain(account_id)


@router.get("/system/llm", response_model=LLMStatus)
async def llm_status():
    return explanation_service.service.status()
```

with `from app.services import explanation_service` and `from app.schemas.api import ExplanationOut, LLMStatus` imported, and remove `AIReportResponse` from the `app.schemas.graph` import (delete that class from `app/schemas/graph.py` too — nothing else uses it).

In `app/api/main.py` lifespan, after the `warm = ...` line add:

```python
    await explanation_service.configure(settings)
```

with `from app.services import explanation_service` imported.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_explanation_service.py -q -p no:cacheprovider` — Expected: 7 passed.
Run: `python3 -m pytest -q -p no:cacheprovider` — Expected: all green.

- [ ] **Step 7: Live check (rule-based, then optionally Ollama) and commit**

Restart uvicorn. `curl -s localhost:8000/api/system/llm` → `{"provider":"none","model":null,"reachable":false}` (nothing installed). Pick an alert's `primary_account` and `curl -s localhost:8000/api/accounts/<id>/enrich | python3 -m json.tool` → `provider: none`, non-empty `explanation` mentioning the score.

Optional local-model check: `brew install ollama && ollama serve &` then `ollama pull llama3.2`, restart uvicorn → `/api/system/llm` reports `ollama` / `llama3.2` and `/enrich` returns model-written text with `provider: ollama`.

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add app/services/explanation_service.py app/services/ai_enrichment.py app/core/config.py app/api/endpoints.py app/api/main.py app/schemas/graph.py tests/test_explanation_service.py
git commit -m "feat(ai): pluggable explanation service — Ollama, Anthropic, rule-based fallback" -m "Replaces the placeholder enrichment stub. Also fixes the aggregator's low-confidence path, which attribute-accessed a dict." -m "$TRAILER"
```

---

### Task 13: Live smoke test and final verification

**Files:**
- Create: `tests/test_api_smoke.py`

**Interfaces:**
- Consumes: every route added in Tasks 7–12.

- [ ] **Step 1: Write the gated smoke test**

```python
"""End-to-end read-path smoke against the live stack (Neo4j + Postgres with the
IBM graph loaded). Skips unless POSTGRES_DSN points at the dev container:

    POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
        python3 -m pytest tests/test_api_smoke.py -v
"""
import os
import time
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    "changeme@" not in os.environ.get("POSTGRES_DSN", ""),
    reason="live stack not configured (POSTGRES_DSN)")


@pytest.fixture(scope="module")
def client():
    from app.api.main import app
    try:
        with TestClient(app) as c:
            deadline = time.time() + 120
            while c.get("/api/stats/overview").status_code == 503 and time.time() < deadline:
                time.sleep(2)
            yield c
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"live stack unavailable: {exc}")


def test_overview_and_series(client):
    ov = client.get("/api/stats/overview").json()
    assert ov["transactions"] > 1_000_000 and ov["currencies"][0]["code"] == "US Dollar"
    assert ov["dataset"]["end_ts"] == 1663517880
    s = client.get("/api/stats/volume-series?currency=US%20Dollar&period=all").json()
    assert s["bucket_seconds"] == 86400 and s["volume"][-1] > 0


def test_alerts_transactions_graph_chain(client):
    alerts = client.get("/api/alerts?limit=3&min_level=high").json()
    assert alerts["total"] > 0 and alerts["items"][0]["explanation"]
    acct = alerts["items"][0]["primary_account"]
    node = client.get(f"/api/graph/account/{acct}").json()
    assert node["id"] == acct and node["gnn_risk_score"] is not None
    tx = client.get(f"/api/transactions?account_id={acct}&limit=5").json()["items"]
    assert tx and all(t["ts"] > 0 for t in tx)
    latest = client.get("/api/transactions?limit=5").json()["items"]
    assert [t["ts"] for t in latest] == sorted((t["ts"] for t in latest), reverse=True)
    sub = client.get(f"/api/graph/subgraph?account_id={acct}&depth=1").json()
    assert sub["nodes"] and any(n["data"]["risk_tier"] != "low" for n in sub["nodes"])


def test_pipeline_alias_and_llm(client):
    assert client.get("/api/pipeline/threshold").json()["default"] >= 0.5
    curve = client.get("/api/pipeline/metrics/curve?points=5").json()
    assert curve["loaded"] is True and len(curve["cutoffs"]) == 5
    llm = client.get("/api/system/llm").json()
    assert llm["provider"] in ("none", "ollama", "anthropic")
    acct = client.get("/api/alerts?limit=1").json()["items"][0]["primary_account"]
    ex = client.get(f"/api/accounts/{acct}/enrich").json()
    assert ex["account_id"] == acct and ex["explanation"] and ex["risk_level"] in ("low", "medium", "high", "critical")
```

- [ ] **Step 2: Run it against the stack**

Run: `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' python3 -m pytest tests/test_api_smoke.py -v -p no:cacheprovider`
Expected: 3 passed (the first takes up to ~20 s while the stats cache builds).

- [ ] **Step 3: Full suite, both ways**

Run: `python3 -m pytest -q -p no:cacheprovider` — Expected: all passed; smoke + meta tests skipped.
Run: `POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' python3 -m pytest -q -p no:cacheprovider` — Expected: all passed, only the Elliptic-dataset tests skipped.

- [ ] **Step 4: Commit**

```bash
TRAILER=$'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_018xPxkAKNFHoTWFQ7fMoqkc'
git add tests/test_api_smoke.py
git commit -m "test(api): live smoke over stats, alerts, transactions, graph, pipeline, explain" -m "$TRAILER"
```

Stop the background uvicorn when done (`kill %1` or the PID) unless Plan B starts immediately.

---

## Self-review against the spec

- §6.1 overview → Task 7; §6.2 series → Tasks 6–7; §6.3 cache/warm-up/invalidate → Task 7 (runner hook included); §6.4–6.5 alerts → Tasks 4 + 8; §6.6 transactions + index → Tasks 2 + 9; §6.7 graph fixes + account route → Task 10; §6.8 aliases + curve → Task 11; §6.9 enrich → Task 12; §7 explanation service → Task 12; `app_meta` (§6.1 dataset, §6.10) → Task 4; mock purge and agent removal (§2, §5) → Tasks 1, 3.
- §6.10 upload, §8 frontend, §9 snapshot, §12 docs are Plans B–D.
- Deviation noted: evidence carries the raw `pagerank_score`, not a percentile (a whole-graph rank per request is out of scope); the spec's `pagerank percentile` wording is satisfied by the raw score in the prompt.
- Names used across tasks: `stats_service.cache/warmup/invalidate/currency_code/build_series/period_window`; `alerts_service.shape_alert/list_alerts/set_status`; `transactions_service.list_latest/list_for_account/shape_transaction/max_tier`; `GraphService.node_data/get_account/get_flow_between`; `explanation_service.service/configure/build_evidence/evidence_prompt/rule_based/resolve_provider/check_ollama`; `PostgresClient.count_risk_flags/update_risk_flag_status/count_open_flags_by_type/get_account_ids_for_flag_type/ensure_app_meta_table/get_app_meta/set_app_meta`; `app.viz.router.pages/api/router` — consistent in every task above.
