"""End-to-end smoke for the /viz backend against the live stack.

Gated: needs Docker (Neo4j + Postgres) up and POSTGRES_DSN pointing at the dev
container, else it skips. Seeds a tiny known cycle + a marked flag directly
(running the real PageRank/Louvain over the full 513k-node graph is far too slow
for a test), then exercises the real HTTP read path and the /run job lifecycle.

    POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
        python3 -m pytest tests/test_viz_smoke.py -v
"""
import asyncio
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from config import NEO4J_DATABASE

_MIGRATION = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed')),
    stage TEXT, progress DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb, error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
);
"""

async def _seed():
    from db.neo4j import Neo4jClient
    from db.postgres import PostgresClient
    n = Neo4jClient()
    await n.initialize()
    async with n.driver.session(database=NEO4J_DATABASE) as s:
        await (await s.run("MATCH (a:Account) WHERE a.id STARTS WITH 'e2e_' DETACH DELETE a")).consume()
        await (await s.run(
            "MERGE (a:Account {id:'e2e_a'}) SET a.in_cycle=true, a.gnn_risk_score=0.95, a.community_id='e2e_c' "
            "MERGE (b:Account {id:'e2e_b'}) SET b.in_cycle=true, b.gnn_risk_score=0.90, b.community_id='e2e_c' "
            "MERGE (a)-[e:FLOWS_TO]->(b) SET e.total_amount=300000.0, e.tx_count=5"
        )).consume()
    await n.close()

    pg = PostgresClient()
    await pg.initialize()
    async with pg._get_connection() as conn:
        await conn.execute(_MIGRATION)
        await conn.execute("DELETE FROM pipeline_runs WHERE status IN ('queued','running')")
        await conn.execute("DELETE FROM risk_flags WHERE fingerprint = 'agg:e2e_a'")
    await pg.upsert_risk_flag(
        flag_type="AGGREGATE", fingerprint="agg:e2e_a", account_ids=["e2e_a"],
        risk_level="critical", risk_score=0.95,
        explanation="Marked (GNN risk 0.95, member of a detected cycle).",
        details={"signals": {"gnn": True, "cycle": True, "community": False}, "gnn_score": 0.95},
    )
    await pg.close()


async def _cleanup():
    from db.neo4j import Neo4jClient
    from db.postgres import PostgresClient
    n = Neo4jClient()
    await n.initialize()
    async with n.driver.session(database=NEO4J_DATABASE) as s:
        await (await s.run("MATCH (a:Account) WHERE a.id STARTS WITH 'e2e_' DETACH DELETE a")).consume()
    await n.close()
    pg = PostgresClient()
    await pg.initialize()
    async with pg._get_connection() as conn:
        await conn.execute("DELETE FROM risk_flags WHERE fingerprint = 'agg:e2e_a'")
    await pg.close()


@pytest.fixture
def live_client():
    try:
        asyncio.run(_seed())
    except Exception as exc:
        pytest.skip(f"live stack unavailable: {exc}")
    from app.api.main import app
    from app.viz import deps
    with TestClient(app) as c:
        if deps.pg() is None:
            pytest.skip("deps not initialized")
        yield c
    try:
        asyncio.run(_cleanup())
    except Exception:
        pass


def test_read_path_and_run_lifecycle(live_client):
    c = live_client

    # --- read path: HTTP -> store -> real Neo4j ---
    sub = c.get("/viz/subgraph?account_id=e2e_a&hops=2").json()
    ids = {n["data"]["id"] for n in sub["nodes"]}
    assert {"e2e_a", "e2e_b"} <= ids
    assert any(n["data"]["in_cycle"] for n in sub["nodes"])
    edge = next(e for e in sub["edges"] if e["data"]["id"] == "e2e_a->e2e_b")
    assert edge["data"]["weight"] == 3.0                       # 300000/100000, thickness ∝ amount

    # --- marked: HTTP -> store -> real Postgres ---
    marked = c.get("/viz/marked").json()
    assert any(m["account_id"] == "e2e_a" for m in marked)

    # --- run lifecycle: queued -> completed via a fast fake runner + real pg ---
    from app.viz import deps
    real_pg = deps.pg()

    async def fake_run(run_id):
        await real_pg.update_pipeline_run(run_id, status="completed", progress=1.0,
                                          counts={"marked": 1}, finished=True)

    with patch("app.viz.router.PipelineRunner") as PR:
        PR.return_value.run = fake_run
        rid = c.post("/viz/run").json()["run_id"]
    status = c.get(f"/viz/run/{rid}").json()
    assert status["status"] == "completed" and status["counts"]["marked"] == 1
