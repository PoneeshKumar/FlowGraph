"""
Real-Neo4j integration tests for the Louvain data path:
export_flows_to_edges (window filtering, aggregate fields) and
write_community_assignments (batched node-property writes).

Connects to the Neo4j configured via NEO4J_URI / NEO4J_PASSWORD (the
docker-compose instance). If no Neo4j is reachable, the module is skipped.
Isolation: every account id is prefixed ITEST_LV_ and wiped before/after.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from db.neo4j import Neo4jClient, NEO4J_DATABASE

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PREFIX = "ITEST_LV_"
_NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


async def _reachable(client: Neo4jClient) -> bool:
    try:
        await client.initialize()
        async with client.driver.session(database=NEO4J_DATABASE) as s:
            await s.run("RETURN 1")
        return True
    except Exception:
        return False


async def _wipe(client: Neo4jClient) -> None:
    async with client.driver.session(database=NEO4J_DATABASE) as s:
        await s.run(
            "MATCH (a:Account) WHERE a.id STARTS WITH $p DETACH DELETE a",
            p=_PREFIX,
        )


@pytest_asyncio.fixture
async def neo4j():
    client = Neo4jClient()
    if not await _reachable(client):
        await client.close()
        pytest.skip("No Neo4j reachable at NEO4J_URI — start `docker compose up neo4j`")
    await client.init_constraints()
    await _wipe(client)
    yield client
    await _wipe(client)
    await client.close()


async def _seed_edge(client, src, dst, amount_cents, ts, txn_id):
    await client.upsert_transaction_graph(
        sender_id=f"{_PREFIX}{src}",
        receiver_id=f"{_PREFIX}{dst}",
        amount_cents=amount_cents,
        timestamp_utc=ts,
        rail="WIRE",
        event_type="SETTLEMENT",
        transaction_id=f"{_PREFIX}{txn_id}",
        idempotency_key=f"{_PREFIX}{txn_id}",
    )


def _itest_only(edges):
    """The shared docker Neo4j may hold benchmark data — filter to our namespace."""
    return [e for e in edges if e["src"].startswith(_PREFIX) and e["dst"].startswith(_PREFIX)]


class TestExportFlowsToEdges:
    async def test_returns_aggregates_and_respects_window(self, neo4j):
        # Two txns A→B inside the window (aggregate: 30_000 cents, tx_count 2),
        # one txn C→D far outside it.
        await _seed_edge(neo4j, "A", "B", 10_000, _NOW - timedelta(days=1), "ab1")
        await _seed_edge(neo4j, "A", "B", 20_000, _NOW - timedelta(days=2), "ab2")
        await _seed_edge(neo4j, "C", "D", 50_000, _NOW - timedelta(days=90), "cd1")

        edges = _itest_only(
            await neo4j.export_flows_to_edges(window_days=30, reference_time=_NOW)
        )

        assert len(edges) == 1
        edge = edges[0]
        assert edge["src"] == f"{_PREFIX}A"
        assert edge["dst"] == f"{_PREFIX}B"
        assert edge["total_amount"] == 30_000
        assert edge["tx_count"] == 2

    async def test_stale_edge_included_when_window_covers_it(self, neo4j):
        await _seed_edge(neo4j, "C", "D", 50_000, _NOW - timedelta(days=90), "cd1")

        edges = _itest_only(
            await neo4j.export_flows_to_edges(window_days=120, reference_time=_NOW)
        )

        assert len(edges) == 1
        assert edges[0]["src"] == f"{_PREFIX}C"


class TestWriteCommunityAssignments:
    async def test_writes_props_and_returns_count(self, neo4j):
        await _seed_edge(neo4j, "A", "B", 10_000, _NOW - timedelta(days=1), "ab1")

        detected_at = int(_NOW.timestamp())
        written = await neo4j.write_community_assignments(
            {f"{_PREFIX}A": "abc123def456", f"{_PREFIX}B": "abc123def456"},
            detected_at_epoch=detected_at,
        )

        assert written == 2
        async with neo4j.driver.session(database=NEO4J_DATABASE) as s:
            result = await s.run(
                "MATCH (a:Account) WHERE a.id STARTS WITH $p "
                "RETURN a.id AS id, a.community_id AS cid, "
                "a.community_detected_at AS ts ORDER BY id",
                p=_PREFIX,
            )
            records = [r async for r in result]

        assert [r["cid"] for r in records] == ["abc123def456", "abc123def456"]
        assert all(r["ts"] == detected_at for r in records)

    async def test_empty_assignments_writes_nothing(self, neo4j):
        written = await neo4j.write_community_assignments({}, detected_at_epoch=0)
        assert written == 0

    async def test_batching_splits_large_maps(self, neo4j):
        # Seed 7 accounts, write with batch_size=3 → 3 transactions, all rows land.
        for i in range(7):
            await _seed_edge(neo4j, f"N{i}", f"N{(i + 1) % 7}", 10_000,
                             _NOW - timedelta(days=1), f"n{i}")

        assignments = {f"{_PREFIX}N{i}": "fff000fff000" for i in range(7)}
        written = await neo4j.write_community_assignments(
            assignments, detected_at_epoch=int(_NOW.timestamp()), batch_size=3
        )

        assert written == 7
        async with neo4j.driver.session(database=NEO4J_DATABASE) as s:
            result = await s.run(
                "MATCH (a:Account) WHERE a.id STARTS WITH $p "
                "AND a.community_id = 'fff000fff000' RETURN count(a) AS n",
                p=_PREFIX,
            )
            record = await result.single()
        assert record["n"] == 7
