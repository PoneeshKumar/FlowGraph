"""Tests for live per-event scoring assembly + orchestration."""
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.incremental_scorer import (
    assemble_neighborhood_features, IncrementalScorer)


def _node(nid, comm=1, pr=0.01):
    return {"id": nid, "labels": ["Account"], "pagerank_score": pr, "community_id": comm,
            "created_at": None, "kyc_tier": None, "risk_score": None, "country": None,
            "account_age": None, "cumulative_volume": None}


def _edge(src, dst, amt=1000.0, tx=3, first_ts=100, last_ts=200):
    return {"src": src, "dst": dst, "total_amount": amt, "tx_count": tx,
            "first_ts": first_ts, "last_ts": last_ts}


def test_assemble_produces_47col_featureset_over_neighborhood():
    nodes = [_node("a"), _node("b"), _node("c")]
    edges = [_edge("a", "b"), _edge("b", "c")]
    fs = assemble_neighborhood_features(nodes, edges)
    assert fs.num_features == 47                         # the trained column contract
    assert {"a", "b", "c"} <= set(fs.node_ids)
    assert fs.x.shape == (fs.num_nodes, 47)
    assert fs.edge_index.shape[0] == 2


def test_assemble_uses_redis_volumes_when_supplied():
    nodes = [_node("a"), _node("b")]
    edges = [_edge("a", "b")]
    # a volume map for account "a"; assembly must not error and must keep column count
    volumes = {"a": {"volume_out_1h": 500.0, "txn_out_1h": 2.0, "volume_in_24h": 900.0}}
    fs = assemble_neighborhood_features(nodes, edges, volumes=volumes)
    assert fs.num_features == 47
    assert np.isfinite(fs.x).all()                       # no NaNs leak into the model


# ---- IncrementalScorer.score_touched (mocked stores + stub scorer) ----------

def _scorer(const=0.7):
    s = MagicMock()
    s.score = lambda fs: np.full(fs.num_nodes, const, dtype=np.float32)
    return s


def _neo4j(nodes, edges):
    n = MagicMock()
    n.export_neighborhood = AsyncMock(return_value=(nodes, edges))
    n.write_gnn_scores = AsyncMock()
    return n


@pytest.mark.asyncio
async def test_score_touched_writes_scores_and_seed_flags():
    nodes = [_node("a"), _node("b"), _node("c")]
    edges = [_edge("a", "b"), _edge("b", "c")]
    neo4j = _neo4j(nodes, edges)
    pg = MagicMock(upsert_risk_flag=AsyncMock())
    scorer = IncrementalScorer(neo4j, MagicMock(), pg, _scorer(0.7),
                               hops=3, fanout=10, max_affected=250)

    mapping = await scorer.score_touched({"a", "b"})

    # neighborhood exported with the seeds + caps
    args = neo4j.export_neighborhood.await_args.args
    assert set(args[0]) == {"a", "b"} and args[3] == 250          # seeds, node_cap
    # every affected account's score written back
    written = neo4j.write_gnn_scores.await_args.args[0]
    assert {"a", "b", "c"} <= set(written) and all(abs(v - 0.7) < 1e-6 for v in written.values())
    # a LIVE_GNN flag only for the touched seeds (a, b) — not the neighbor c
    flagged = {c.kwargs["account_ids"][0] for c in pg.upsert_risk_flag.await_args_list}
    assert flagged == {"a", "b"}
    assert all(c.kwargs["flag_type"] == "LIVE_GNN" for c in pg.upsert_risk_flag.await_args_list)
    assert {"a", "b", "c"} <= set(mapping)


@pytest.mark.asyncio
async def test_score_touched_noops_on_empty_and_missing():
    neo4j = _neo4j([], [])
    scorer = IncrementalScorer(neo4j, MagicMock(),
                               MagicMock(upsert_risk_flag=AsyncMock()), _scorer())
    assert await scorer.score_touched([]) == {}              # no seeds
    neo4j.export_neighborhood.assert_not_awaited()
    assert await scorer.score_touched({"x"}) == {}            # empty neighborhood
    neo4j.write_gnn_scores.assert_not_awaited()
