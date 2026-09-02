"""Integration tests for the viz Neo4j result writers.

Uses the real Neo4j container (AUTH none). Gated: skips if unreachable. Seeds
three throwaway Account nodes (ids a/b/c) and cleans them up.
"""
import pytest
import pytest_asyncio

from ml.predict import risk_level
from config import NEO4J_DATABASE

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def neo4j_seeded():
    from db.neo4j import Neo4jClient
    client = Neo4jClient()
    try:
        await client.initialize()
    except Exception as exc:
        pytest.skip(f"Neo4j not reachable: {exc}")
    async with client.driver.session(database=NEO4J_DATABASE) as s:
        await (await s.run("MATCH (a:Account) WHERE a.id IN ['a','b','c'] DETACH DELETE a")).consume()
        await (await s.run("UNWIND ['a','b','c'] AS x CREATE (:Account {id:x})")).consume()
    yield client
    async with client.driver.session(database=NEO4J_DATABASE) as s:
        await (await s.run("MATCH (a:Account) WHERE a.id IN ['a','b','c'] DETACH DELETE a")).consume()
    await client.close()


async def test_write_gnn_scores(neo4j_seeded):
    n = await neo4j_seeded.write_gnn_scores({"a": 0.95, "b": 0.1}, tier_of=risk_level)
    assert n == 2
    async with neo4j_seeded.driver.session(database=NEO4J_DATABASE) as s:
        rec = await (await s.run(
            "MATCH (x:Account {id:'a'}) RETURN x.gnn_risk_score AS s, x.gnn_risk_tier AS t"
        )).single()
    assert rec["s"] == 0.95 and rec["t"] == "critical"


async def test_write_cycle_membership_refreshes(neo4j_seeded):
    await neo4j_seeded.write_cycle_membership(["a", "b"])
    await neo4j_seeded.write_cycle_membership(["a"])  # b must flip back to false
    async with neo4j_seeded.driver.session(database=NEO4J_DATABASE) as s:
        res = await s.run("MATCH (x:Account) WHERE x.id IN ['a','b'] RETURN x.id AS id, x.in_cycle AS c")
        rows = {r["id"]: r["c"] async for r in res}
    assert rows["a"] is True and rows["b"] is False
