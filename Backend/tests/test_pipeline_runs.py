"""Integration tests for the pipeline_runs job-status methods.

Uses a real Postgres (the dev container). Gated: if the DB is unreachable the
module is skipped, so mock-only environments still pass. Run against the dev
container with:

    POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
        python3 -m pytest tests/test_pipeline_runs.py -v
"""
import pytest
import pytest_asyncio

from db.postgres import PostgresClient

pytestmark = pytest.mark.asyncio

_MIGRATION = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed')),
    stage TEXT,
    progress DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
"""


@pytest_asyncio.fixture
async def pg_client():
    client = PostgresClient()
    try:
        await client.initialize()
    except Exception as exc:  # DB not up / wrong creds
        pytest.skip(f"Postgres not reachable: {exc}")
    async with client._get_connection() as conn:
        await conn.execute(_MIGRATION)
        await conn.execute("DELETE FROM pipeline_runs")  # deterministic latest/active
    yield client
    await client.close()


async def test_create_and_fetch_run(pg_client):
    run_id = await pg_client.create_pipeline_run()
    row = await pg_client.get_pipeline_run(run_id)
    assert row["id"] == run_id and row["status"] == "queued"
    active = await pg_client.get_active_pipeline_run()
    assert active["id"] == run_id


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


async def test_no_op_update_is_safe(pg_client):
    run_id = await pg_client.create_pipeline_run()
    await pg_client.update_pipeline_run(run_id)  # nothing to set
    row = await pg_client.get_pipeline_run(run_id)
    assert row["status"] == "queued"


async def test_missing_run_returns_none(pg_client):
    assert await pg_client.get_pipeline_run("00000000-0000-0000-0000-000000000000") is None
