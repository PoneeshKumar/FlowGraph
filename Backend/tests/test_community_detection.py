"""
Unit tests for the Louvain community detection engine (fraud/community_detector.py).

All tests here are pure — no Neo4j/Postgres. The orchestration tests use fake
clients. Real-Neo4j coverage lives in tests/test_neo4j_louvain_integration.py.
"""

from __future__ import annotations

import math

import pytest

from fraud.community_detector import (
    CommunityDetector,
    build_undirected_graph,
    community_conductance,
    community_fingerprint,
    core_members,
    edge_weight,
    score_community,
    split_disconnected,
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


# ---------------------------------------------------------------------------
# split_disconnected + community_conductance (pure helpers)
# ---------------------------------------------------------------------------

class TestSplitDisconnected:
    def test_connected_community_passes_through(self):
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
            {"src": "B", "dst": "C", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        assert split_disconnected([{"A", "B", "C"}], g) == [{"A", "B", "C"}]

    def test_disconnected_community_is_split(self):
        # One 'community' spanning two disjoint edges must break into two pieces.
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
            {"src": "C", "dst": "D", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        out = split_disconnected([{"A", "B", "C", "D"}], g)
        assert {frozenset(s) for s in out} == {frozenset({"A", "B"}), frozenset({"C", "D"})}


class TestCommunityConductance:
    def test_isolated_component_has_zero_conductance(self):
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
            {"src": "C", "dst": "D", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        assert community_conductance(g, {"A", "B"}) == 0.0

    def test_whole_graph_conductance_is_zero_not_error(self):
        # S == all nodes → empty complement → nx.conductance divides by zero;
        # the helper must guard and return 0.0, not raise.
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        assert community_conductance(g, {"A", "B"}) == 0.0


# ---------------------------------------------------------------------------
# CommunityDetector orchestration (fake clients)
# ---------------------------------------------------------------------------

class FakeNeo4j:
    def __init__(self, edges):
        self.edges = edges
        self.assignments = None
        self.detected_at = None

    async def export_flows_to_edges(self, **kwargs):
        return self.edges

    async def write_community_assignments(self, assignments, detected_at_epoch, **kwargs):
        self.assignments = assignments
        self.detected_at = detected_at_epoch
        return len(assignments)


class FakePostgres:
    def __init__(self, flagged=()):
        self.flagged = list(flagged)
        self.upserts = []
        self.lookup_kwargs = None

    async def get_flagged_account_ids(self, **kwargs):
        self.lookup_kwargs = kwargs
        return self.flagged

    async def upsert_risk_flag(self, **kwargs):
        self.upserts.append(kwargs)


def _two_community_edges():
    """
    Two disconnected clusters (Louvain must separate disconnected components):
      - suspicious: 8 accounts S0..S7, complete graph, $50k per corridor,
        S0 and S1 already flagged by the cycle detector
      - benign: B0—B1—B2 chain, $200 per corridor
    """
    edges = []
    suspicious = [f"S{i}" for i in range(8)]
    for i in range(8):
        for j in range(i + 1, 8):
            edges.append({
                "src": suspicious[i], "dst": suspicious[j],
                "total_amount": 5_000_000, "tx_count": 3,
            })
    edges.append({"src": "B0", "dst": "B1", "total_amount": 20_000, "tx_count": 1})
    edges.append({"src": "B1", "dst": "B2", "total_amount": 20_000, "tx_count": 1})
    return edges


@pytest.mark.asyncio
async def test_detector_flags_suspicious_community_only():
    neo4j = FakeNeo4j(_two_community_edges())
    postgres = FakePostgres(flagged=["S0", "S1"])
    detector = CommunityDetector(neo4j, postgres)

    result = await detector.run()

    # Both communities kept (benign trio meets MIN_COMMUNITY_SIZE=3) …
    assert result["communities"] == 2
    # … all 11 accounts got node-property assignments …
    assert result["assignments"] == 11
    assert set(neo4j.assignments.keys()) == {f"S{i}" for i in range(8)} | {"B0", "B1", "B2"}
    # … but only the dense high-volume corroborated cluster was flagged.
    assert len(postgres.upserts) == 1
    flag = postgres.upserts[0]
    assert flag["flag_type"] == "COMMUNITY"
    assert sorted(flag["account_ids"]) == [f"S{i}" for i in range(8)]
    assert flag["risk_level"] == "critical"
    assert flag["explanation"]
    assert flag["details"]["community_id"] == flag["fingerprint"][:12]
    assert flag["details"]["core_members"]


@pytest.mark.asyncio
async def test_detector_excludes_own_flag_type_from_overlap():
    neo4j = FakeNeo4j(_two_community_edges())
    postgres = FakePostgres()
    detector = CommunityDetector(neo4j, postgres)

    await detector.run()

    assert postgres.lookup_kwargs == {"status": "open", "exclude_flag_type": "COMMUNITY"}


@pytest.mark.asyncio
async def test_detector_assigns_members_of_same_community_same_id():
    neo4j = FakeNeo4j(_two_community_edges())
    detector = CommunityDetector(neo4j, FakePostgres())

    await detector.run()

    s_ids = {neo4j.assignments[f"S{i}"] for i in range(8)}
    b_ids = {neo4j.assignments[b] for b in ("B0", "B1", "B2")}
    assert len(s_ids) == 1
    assert len(b_ids) == 1
    assert s_ids != b_ids


@pytest.mark.asyncio
async def test_detector_skips_undersized_communities():
    edges = [{"src": "X", "dst": "Y", "total_amount": 5_000_000, "tx_count": 2}]
    neo4j = FakeNeo4j(edges)
    detector = CommunityDetector(neo4j, FakePostgres())

    result = await detector.run()

    assert result["communities"] == 0
    assert result["assignments"] == 0
    assert neo4j.assignments == {}


@pytest.mark.asyncio
async def test_detector_empty_graph_is_a_noop():
    neo4j = FakeNeo4j([])
    postgres = FakePostgres()
    detector = CommunityDetector(neo4j, postgres)

    result = await detector.run()

    assert result == {"communities": 0, "assignments": 0, "flags": []}
    assert postgres.upserts == []


@pytest.mark.asyncio
async def test_detector_without_postgres_still_returns_flags():
    neo4j = FakeNeo4j(_two_community_edges())
    detector = CommunityDetector(neo4j, postgres_client=None)

    result = await detector.run()

    # No overlap signal (max composite 0.65) but volume+density+size still
    # clear the medium bar; flags are computed and returned, just not persisted.
    assert len(result["flags"]) == 1
    assert result["flags"][0]["risk_level"] in ("medium", "high")
