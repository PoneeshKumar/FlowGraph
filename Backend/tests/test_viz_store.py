"""Tests for app.viz.store — pure shaping (always), mocked list_marked, and
gated Neo4j reads against a seeded 3-node cycle."""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from app.viz import store
from config import NEO4J_DATABASE


# ---- pure: shape_elements -------------------------------------------------

def test_shape_elements_edge_weight_and_direction():
    nodes = [{"id": "a", "pagerank_score": 0.04}, {"id": "b"}]
    rels = [{"source": "a", "target": "b", "total_amount": 250000.0, "tx_count": 8}]
    out = store.shape_elements(nodes, rels)
    e = out["edges"][0]["data"]
    assert e["source"] == "a" and e["target"] == "b"   # direction → arrowhead
    assert e["weight"] == 2.5                            # 250000/100000, thickness ∝ amount
    assert {n["data"]["id"] for n in out["nodes"]} == {"a", "b"}


def test_shape_elements_dedups():
    nodes = [{"id": "a"}, {"id": "a"}]
    rels = [{"source": "a", "target": "b"}, {"source": "a", "target": "b"}]
    out = store.shape_elements(nodes, rels)
    assert len(out["nodes"]) == 1 and len(out["edges"]) == 1


# ---- mocked pg: list_marked ----------------------------------------------

@pytest.mark.asyncio
async def test_list_marked_filters_and_sorts():
    flags = [
        {"account_ids": ["x"], "risk_score": 0.9, "explanation": "e1",
         "details": {"signals": {"gnn": True, "cycle": False, "community": False}, "gnn_score": 0.9}},
        {"account_ids": ["y"], "risk_score": 0.6, "explanation": "e2",
         "details": {"signals": {"gnn": False, "cycle": True, "community": False}}},
    ]
    pg = MagicMock(get_risk_flags=AsyncMock(return_value=flags))
    rows = await store.list_marked(pg, sort="score")
    assert [r["account_id"] for r in rows] == ["x", "y"]     # score desc
    assert rows[0]["signals"]["gnn"] is True
    cyc = await store.list_marked(pg, signal="cycle")
    assert [r["account_id"] for r in cyc] == ["y"]           # signal filter


# ---- gated Neo4j: load_subgraph + list_communities -----------------------

@pytest_asyncio.fixture
async def neo4j_cycle():
    from db.neo4j import Neo4jClient
    client = Neo4jClient()
    try:
        await client.initialize()
    except Exception as exc:
        pytest.skip(f"Neo4j not reachable: {exc}")
    nodes_q = ("UNWIND ['vt_a','vt_b','vt_c'] AS x MERGE (n:Account {id:x}) "
               "SET n.community_id='vt_comm', n.gnn_risk_score=0.99, n.pagerank_score=0.05")
    edges_q = ("MATCH (a:Account {id:'vt_a'}),(b:Account {id:'vt_b'}),(c:Account {id:'vt_c'}) "
               "MERGE (a)-[e1:FLOWS_TO]->(b) SET e1.total_amount=250000.0, e1.tx_count=8 "
               "MERGE (b)-[e2:FLOWS_TO]->(c) SET e2.total_amount=248000.0, e2.tx_count=7 "
               "MERGE (c)-[e3:FLOWS_TO]->(a) SET e3.total_amount=245000.0, e3.tx_count=6")
    async with client.driver.session(database=NEO4J_DATABASE) as s:
        await (await s.run("MATCH (a:Account) WHERE a.id IN ['vt_a','vt_b','vt_c'] DETACH DELETE a")).consume()
        await (await s.run(nodes_q)).consume()
        await (await s.run(edges_q)).consume()
    yield client
    async with client.driver.session(database=NEO4J_DATABASE) as s:
        await (await s.run("MATCH (a:Account) WHERE a.id IN ['vt_a','vt_b','vt_c'] DETACH DELETE a")).consume()
    await client.close()


@pytest.mark.asyncio
async def test_load_subgraph_account_cycle(neo4j_cycle):
    def factory():
        return neo4j_cycle.driver.session(database=NEO4J_DATABASE)
    out = await store.load_subgraph(factory, account_id="vt_a", hops=3)
    assert {n["data"]["id"] for n in out["nodes"]} == {"vt_a", "vt_b", "vt_c"}
    edges = {(e["data"]["source"], e["data"]["target"]) for e in out["edges"]}
    assert ("vt_a", "vt_b") in edges and ("vt_c", "vt_a") in edges
    ab = next(e for e in out["edges"] if e["data"]["id"] == "vt_a->vt_b")
    assert ab["data"]["weight"] == 2.5                       # 250000 → thickest


@pytest.mark.asyncio
async def test_list_communities_top_is_seeded(neo4j_cycle):
    def factory():
        return neo4j_cycle.driver.session(database=NEO4J_DATABASE)
    rows = await store.list_communities(factory, sort="risk", limit=5)
    # No pipeline has scored real accounts, so the 0.99-risk seeded community tops the list.
    assert rows and rows[0]["community_id"] == "vt_comm"
    assert rows[0]["size"] == 3 and rows[0]["risk_tier"] == "critical"
