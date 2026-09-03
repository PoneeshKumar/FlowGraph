"""Tests for live per-event scoring assembly + orchestration."""
import numpy as np

from app.services.incremental_scorer import assemble_neighborhood_features


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
