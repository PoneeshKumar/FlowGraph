"""Tests for app.viz.store.

Pure shaping tests always run. The async paths use asyncio.run() (self-contained
loops) rather than pytest-asyncio: the repo's session-scoped event_loop fixture
plus other tests' asyncio.run() usage make pytest-asyncio's current-loop lookup
order-dependent, and this keeps these robust regardless of suite ordering. The
Neo4j paths seed/clean a throwaway cycle within one loop and skip if unreachable.
"""
import asyncio
import pytest
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


# ---- mocked pg: list_marked (self-contained loop) ------------------------

def test_list_marked_filters_and_sorts():
    flags = [
        {"account_ids": ["x"], "risk_score": 0.9, "explanation": "e1",
         "details": {"signals": {"gnn": True, "cycle": False, "community": False}, "gnn_score": 0.9}},
        {"account_ids": ["y"], "risk_score": 0.6, "explanation": "e2",
         "details": {"signals": {"gnn": False, "cycle": True, "community": False}}},
    ]
    pg = MagicMock(get_risk_flags=AsyncMock(return_value=flags))
    rows = asyncio.run(store.list_marked(pg, sort="score"))
    assert [r["account_id"] for r in rows] == ["x", "y"]     # score desc
    assert rows[0]["signals"]["gnn"] is True
    cyc = asyncio.run(store.list_marked(pg, signal="cycle"))
    assert [r["account_id"] for r in cyc] == ["y"]           # signal filter


# ---- gated Neo4j: load_subgraph + list_communities -----------------------

class _SkipNeo4j(Exception):
    pass


_NODES_Q = ("UNWIND ['vt_a','vt_b','vt_c'] AS x MERGE (n:Account {id:x}) "
            "SET n.community_id='vt_comm', n.gnn_risk_score=0.99, n.pagerank_score=0.05")
_EDGES_Q = ("MATCH (a:Account {id:'vt_a'}),(b:Account {id:'vt_b'}),(c:Account {id:'vt_c'}) "
            "MERGE (a)-[e1:FLOWS_TO]->(b) SET e1.total_amount=250000.0, e1.tx_count=8 "
            "MERGE (b)-[e2:FLOWS_TO]->(c) SET e2.total_amount=248000.0, e2.tx_count=7 "
            "MERGE (c)-[e3:FLOWS_TO]->(a) SET e3.total_amount=245000.0, e3.tx_count=6")
_DEL_Q = "MATCH (a:Account) WHERE a.id IN ['vt_a','vt_b','vt_c'] DETACH DELETE a"


async def _seed_and(fn):
    from db.neo4j import Neo4jClient
    client = Neo4jClient()
    try:
        await client.initialize()
    except Exception as exc:
        raise _SkipNeo4j(str(exc))
    try:
        async with client.driver.session(database=NEO4J_DATABASE) as s:
            await (await s.run(_DEL_Q)).consume()
            await (await s.run(_NODES_Q)).consume()
            await (await s.run(_EDGES_Q)).consume()

        def factory():
            return client.driver.session(database=NEO4J_DATABASE)

        return await fn(factory)
    finally:
        async with client.driver.session(database=NEO4J_DATABASE) as s:
            await (await s.run(_DEL_Q)).consume()
        await client.close()


def _run_neo4j(fn):
    try:
        return asyncio.run(_seed_and(fn))
    except _SkipNeo4j as exc:
        pytest.skip(f"Neo4j not reachable: {exc}")


def test_load_subgraph_account_cycle():
    async def fn(factory):
        return await store.load_subgraph(factory, account_id="vt_a", hops=3)
    out = _run_neo4j(fn)
    assert {n["data"]["id"] for n in out["nodes"]} == {"vt_a", "vt_b", "vt_c"}
    edges = {(e["data"]["source"], e["data"]["target"]) for e in out["edges"]}
    assert ("vt_a", "vt_b") in edges and ("vt_c", "vt_a") in edges
    ab = next(e for e in out["edges"] if e["data"]["id"] == "vt_a->vt_b")
    assert ab["data"]["weight"] == 2.5                       # 250000 → thickest


def test_list_communities_top_is_seeded():
    async def fn(factory):
        return await store.list_communities(factory, sort="risk", limit=5)
    rows = _run_neo4j(fn)
    # No pipeline has scored real accounts, so the 0.99-risk seeded community tops the list.
    assert rows and rows[0]["community_id"] == "vt_comm"
    assert rows[0]["size"] == 3 and rows[0]["risk_tier"] == "critical"
