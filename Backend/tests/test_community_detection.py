"""
Unit tests for the Louvain community detection engine (fraud/community_detector.py).

All tests here are pure — no Neo4j/Postgres. The orchestration tests use fake
clients. Real-Neo4j coverage lives in tests/test_neo4j_louvain_integration.py.
"""

from __future__ import annotations

import math

import pytest

from fraud.community_detector import (
    build_undirected_graph,
    community_fingerprint,
    core_members,
    edge_weight,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# edge_weight
# ---------------------------------------------------------------------------

class TestEdgeWeight:
    def test_log_amount_mode(self):
        assert edge_weight(100, 5, mode="log_amount") == pytest.approx(math.log1p(100))

    def test_amount_mode(self):
        assert edge_weight(2500, 5, mode="amount") == 2500.0

    def test_tx_count_mode(self):
        assert edge_weight(2500, 5, mode="tx_count") == 5.0

    def test_unweighted_mode(self):
        assert edge_weight(2500, 5, mode="unweighted") == 1.0

    def test_negative_amount_clamped_to_zero_weight(self):
        assert edge_weight(-100, 1, mode="log_amount") == 0.0

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            edge_weight(100, 1, mode="bogus")


# ---------------------------------------------------------------------------
# build_undirected_graph
# ---------------------------------------------------------------------------

class TestBuildUndirectedGraph:
    def test_opposite_directions_collapse_to_one_edge(self):
        edges = [
            {"src": "A", "dst": "B", "total_amount": 100, "tx_count": 2},
            {"src": "B", "dst": "A", "total_amount": 50, "tx_count": 1},
        ]
        g = build_undirected_graph(edges, weight_mode="log_amount")
        assert g.number_of_edges() == 1
        attrs = g["A"]["B"]
        # Raw aggregates sum across both directions
        assert attrs["total_amount"] == 150
        assert attrs["tx_count"] == 3
        # Weights are computed per directed record, then summed
        assert attrs["weight"] == pytest.approx(math.log1p(100) + math.log1p(50))

    def test_self_loops_dropped(self):
        edges = [{"src": "A", "dst": "A", "total_amount": 100, "tx_count": 1}]
        g = build_undirected_graph(edges)
        assert g.number_of_nodes() == 0
        assert g.number_of_edges() == 0

    def test_empty_input_gives_empty_graph(self):
        g = build_undirected_graph([])
        assert g.number_of_nodes() == 0

    def test_distinct_pairs_stay_distinct(self):
        edges = [
            {"src": "A", "dst": "B", "total_amount": 100, "tx_count": 1},
            {"src": "B", "dst": "C", "total_amount": 100, "tx_count": 1},
        ]
        g = build_undirected_graph(edges)
        assert g.number_of_edges() == 2
        assert set(g.nodes) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# core_members + community_fingerprint
# ---------------------------------------------------------------------------

def _weighted_graph():
    """A=15, B=11, C=7, D=1 weighted degree."""
    edges = [
        {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 10},
        {"src": "A", "dst": "C", "total_amount": 0, "tx_count": 5},
        {"src": "B", "dst": "C", "total_amount": 0, "tx_count": 1},
        {"src": "C", "dst": "D", "total_amount": 0, "tx_count": 1},
    ]
    return build_undirected_graph(edges, weight_mode="tx_count")


class TestCoreMembers:
    def test_picks_top_k_by_weighted_degree(self):
        g = _weighted_graph()
        assert core_members(g, ["A", "B", "C", "D"], k=2) == ["A", "B"]

    def test_k_larger_than_community_returns_all_sorted(self):
        g = _weighted_graph()
        assert core_members(g, ["C", "D"], k=10) == ["C", "D"]

    def test_degree_computed_within_subgraph_only(self):
        # Restricted to {B, C, D}: B–C (1) and C–D (1) → C has degree 2, B and D have 1.
        g = _weighted_graph()
        assert core_members(g, ["B", "C", "D"], k=1) == ["C"]

    def test_ties_break_lexicographically(self):
        edges = [
            {"src": "X", "dst": "Y", "total_amount": 0, "tx_count": 1},
            {"src": "Y", "dst": "Z", "total_amount": 0, "tx_count": 1},
        ]
        g = build_undirected_graph(edges, weight_mode="tx_count")
        # X and Z tie at weighted degree 1 → X wins lexicographically
        assert core_members(g, ["X", "Y", "Z"], k=2) == ["X", "Y"]


class TestCommunityFingerprint:
    def test_order_invariant(self):
        assert community_fingerprint(["b", "a", "c"]) == community_fingerprint(["c", "a", "b"])

    def test_different_core_different_fingerprint(self):
        assert community_fingerprint(["a", "b"]) != community_fingerprint(["a", "c"])

    def test_is_64_hex_chars(self):
        fp = community_fingerprint(["a"])
        assert len(fp) == 64
        int(fp, 16)  # raises if not hex

    def test_empty_core_raises(self):
        with pytest.raises(ValueError):
            community_fingerprint([])
