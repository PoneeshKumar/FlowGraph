"""
Unit tests for the Louvain community detection engine (fraud/community_detector.py).

All tests here are pure — no Neo4j/Postgres. The orchestration tests use fake
clients. Real-Neo4j coverage lives in tests/test_neo4j_louvain_integration.py.
"""

from __future__ import annotations

import math

import pytest

from fraud.community_detector import build_undirected_graph, edge_weight

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
