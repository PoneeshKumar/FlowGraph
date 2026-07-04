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
    score_community,
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


# ---------------------------------------------------------------------------
# score_community
# ---------------------------------------------------------------------------

class TestScoreCommunity:
    def test_dense_ring_sized_high_volume_flagged_community_is_critical(self):
        # 8 members, complete graph (28 edges, density 1.0), $1.4M internal,
        # 2 members already flagged by other detectors (25% → overlap saturates).
        members = [f"S{i}" for i in range(8)]
        result = score_community(
            member_ids=members,
            internal_edge_count=28,
            internal_total_cents=140_000_000,
            flagged_member_count=2,
        )
        assert result["risk_level"] == "critical"
        assert result["risk_score"] >= 0.85

    def test_small_low_volume_community_is_low(self):
        # 3-node chain, $400 total, nobody flagged.
        result = score_community(
            member_ids=["B0", "B1", "B2"],
            internal_edge_count=2,
            internal_total_cents=40_000,
            flagged_member_count=0,
        )
        assert result["risk_level"] == "low"
        assert result["risk_score"] < 0.40

    def test_overlap_raises_score_monotonically(self):
        members = [f"M{i}" for i in range(10)]
        base = dict(member_ids=members, internal_edge_count=12,
                    internal_total_cents=50_000_000)
        s0 = score_community(flagged_member_count=0, **base)["risk_score"]
        s2 = score_community(flagged_member_count=2, **base)["risk_score"]
        s5 = score_community(flagged_member_count=5, **base)["risk_score"]
        assert s0 < s2 <= s5

    def test_huge_community_scores_low_on_size(self):
        members = [f"H{i}" for i in range(500)]
        result = score_community(
            member_ids=members,
            internal_edge_count=600,
            internal_total_cents=500_000_000,
            flagged_member_count=0,
        )
        assert result["details"]["size_score"] == pytest.approx(0.1)

    def test_explanation_always_nonempty_and_mentions_key_facts(self):
        result = score_community(
            member_ids=["A", "B", "C", "D", "E"],
            internal_edge_count=6,
            internal_total_cents=25_000_000,
            flagged_member_count=1,
        )
        text = result["explanation"]
        assert text
        assert "5 accounts" in text
        assert result["risk_level"] in text

    def test_details_carry_all_five_dimension_scores(self):
        result = score_community(
            member_ids=["A", "B", "C", "D"],
            internal_edge_count=4,
            internal_total_cents=10_000_000,
            flagged_member_count=0,
            conductance=0.3,
        )
        for key in ("size_score", "density_score", "volume_score",
                    "overlap_score", "cohesion_score"):
            assert 0.0 <= result["details"][key] <= 1.0
        assert result["details"]["conductance"] == pytest.approx(0.3)

    def test_higher_conductance_lowers_score(self):
        # A leaky community (much flow crosses the boundary) is less suspicious
        # than an otherwise-identical isolated one.
        base = dict(
            member_ids=[f"M{i}" for i in range(6)],
            internal_edge_count=10,
            internal_total_cents=50_000_000,
            flagged_member_count=1,
        )
        isolated = score_community(conductance=0.0, **base)["risk_score"]
        leaky = score_community(conductance=1.0, **base)["risk_score"]
        assert leaky < isolated

    def test_fewer_than_two_members_raises(self):
        with pytest.raises(ValueError):
            score_community(
                member_ids=["A"],
                internal_edge_count=0,
                internal_total_cents=0,
                flagged_member_count=0,
            )


# ---------------------------------------------------------------------------
# PostgresClient.get_flagged_account_ids (query construction — connection faked)
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_get_flagged_account_ids_excludes_flag_type(monkeypatch):
    from db.postgres import PostgresClient

    client = PostgresClient()
    conn = _FakeConn(rows=[{"account_id": "ACC1"}, {"account_id": "ACC2"}])
    monkeypatch.setattr(client, "_get_connection", lambda: _FakeAcquire(conn))

    ids = await client.get_flagged_account_ids(status="open", exclude_flag_type="COMMUNITY")

    assert ids == ["ACC1", "ACC2"]
    query, args = conn.calls[0]
    assert "flag_type <>" in query
    assert args == ("open", "COMMUNITY")


@pytest.mark.asyncio
async def test_get_flagged_account_ids_without_exclusion(monkeypatch):
    from db.postgres import PostgresClient

    client = PostgresClient()
    conn = _FakeConn(rows=[])
    monkeypatch.setattr(client, "_get_connection", lambda: _FakeAcquire(conn))

    ids = await client.get_flagged_account_ids()

    assert ids == []
    query, args = conn.calls[0]
    assert "flag_type" not in query
    assert args == ("open",)
