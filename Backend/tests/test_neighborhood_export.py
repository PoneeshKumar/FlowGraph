"""Real-Neo4j tests for export_neighborhood — the bounded k-hop export that feeds
live per-event scoring. Skips cleanly if no Neo4j is reachable. All ids are
prefixed ITEST_ and wiped before/after, so benchmark data is never touched."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from db.neo4j import Neo4jClient, NEO4J_DATABASE

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PREFIX = "ITEST_"
_BASE = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

_NODE_KEYS = {"id", "labels", "pagerank_score", "community_id", "created_at",
              "kyc_tier", "risk_score", "country", "account_age", "cumulative_volume"}
_EDGE_KEYS = {"src", "dst", "total_amount", "tx_count", "first_ts", "last_ts"}


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
        await s.run("MATCH (a:Account) WHERE a.id STARTS WITH $p DETACH DELETE a", p=_PREFIX)


@pytest_asyncio.fixture
async def neo4j():
    client = Neo4jClient()
    if not await _reachable(client):
        await client.close()
        pytest.skip("No Neo4j reachable — start `docker compose up neo4j`")
    await client.init_constraints()
    await _wipe(client)
    try:
        yield client
    finally:
        await _wipe(client)
        await client.close()


async def _write(client, src, dst, amount_cents, minutes):
    txn = f"{_PREFIX}txn_{src}_{dst}_{minutes}"
    await client.upsert_transaction_graph(
        sender_id=f"{_PREFIX}{src}", receiver_id=f"{_PREFIX}{dst}",
        amount_cents=amount_cents, timestamp_utc=_BASE + timedelta(minutes=minutes),
        rail="USD", event_type="SETTLEMENT", transaction_id=txn, idempotency_key=txn)


def _ids(nodes):
    return {n["id"].replace(_PREFIX, "") for n in nodes}


async def test_neighborhood_returns_seed_and_khop_neighbors(neo4j):
    # chain A -> B -> C -> D ; seed A, 2 hops (undirected) reaches A,B,C but not D
    await _write(neo4j, "A", "B", 100_000, 0)
    await _write(neo4j, "B", "C", 90_000, 10)
    await _write(neo4j, "C", "D", 80_000, 20)

    nodes, edges = await neo4j.export_neighborhood(
        [f"{_PREFIX}A"], hops=2, fanout=10, node_cap=50)

    ids = _ids(nodes)
    assert {"A", "B", "C"}.issubset(ids)
    assert "D" not in ids                                  # 3 hops away, beyond the bound
    edge_pairs = {(e["src"].replace(_PREFIX, ""), e["dst"].replace(_PREFIX, "")) for e in edges}
    assert ("A", "B") in edge_pairs and ("B", "C") in edge_pairs


async def test_node_cap_bounds_the_set(neo4j):
    # a star: hub sends to 6 leaves; cap at 3 must stop the expansion
    for i in range(6):
        await _write(neo4j, "HUB", f"L{i}", 50_000, i)
    nodes, _ = await neo4j.export_neighborhood(
        [f"{_PREFIX}HUB"], hops=3, fanout=10, node_cap=3)
    assert len(nodes) <= 3
    assert f"{_PREFIX}HUB" in {n["id"] for n in nodes}     # the seed is always kept


async def test_export_matches_feature_builder_contract(neo4j):
    await _write(neo4j, "A", "B", 100_000, 0)
    nodes, edges = await neo4j.export_neighborhood([f"{_PREFIX}A"], hops=1)
    assert nodes and set(nodes[0].keys()) == _NODE_KEYS
    assert edges and set(edges[0].keys()) == _EDGE_KEYS
