"""
Tests for the GNN feature assembly layer (ml/features.py) and the bulk
Redis volume reader it depends on.

_assemble is deliberately I/O-free, so most of this exercises real assembly
logic against hand-built store payloads rather than mocks of itself.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import numpy as np
import pytest

from db.redis import RedisClient, _window_label
from ml.features import (
    COMMUNITY_FEATURES,
    GRAPH_FEATURES,
    NODE_TYPE_FEATURES,
    NUM_CLASSES,
    FeatureBuilder,
)


REF = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc)


def _node(
    account_id: str,
    pagerank: float = 0.0,
    community_id: Any = None,
    created_at: Any = None,
    labels: Any = None,
) -> Dict[str, Any]:
    """A node shaped like one row of export_account_nodes."""
    return {
        "id": account_id,
        "labels": labels if labels is not None else ["Account"],
        "pagerank_score": pagerank,
        "community_id": community_id,
        "created_at": created_at,
        "kyc_tier": None,
        "risk_score": None,
        "country": None,
        "account_age": None,
        "cumulative_volume": None,
    }


def _edge(
    src: str,
    dst: str,
    total_amount: float,
    tx_count: int,
    first_ts: Any = None,
    last_ts: Any = None,
) -> Dict[str, Any]:
    """A row shaped like one record of export_flows_to_edges.

    first_ts/last_ts are unix SECONDS, as the consumer stores them.
    """
    default_ts = int(REF.timestamp())
    return {
        "src": src,
        "dst": dst,
        "total_amount": total_amount,
        "tx_count": tx_count,
        "first_ts": default_ts if first_ts is None else first_ts,
        "last_ts": default_ts if last_ts is None else last_ts,
    }


def _assemble(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    volumes: Dict[str, Dict[str, float]] = None,
    flags: List[Dict[str, Any]] = None,
    windows_hours=(1, 24, 168),
):
    builder = FeatureBuilder(neo4j_client=None)
    return builder._assemble(
        nodes, edges, volumes or {}, flags or [], windows_hours, REF
    )


class TestWindowLabel:
    """Feature names derive from these, so the mapping must not drift."""

    def test_matches_claude_md_naming(self):
        assert _window_label(1) == "1h"
        assert _window_label(24) == "24h"
        assert _window_label(168) == "7d"

    def test_days_only_from_48h_up(self):
        # 24 must stay "24h", not become "1d".
        assert _window_label(48) == "2d"
        assert _window_label(36) == "36h"


class TestStructuralFeatures:
    def test_degree_and_volume_aggregates(self):
        # b receives from a and c, and pays out to d only.
        edges = [
            _edge("a", "b", 1_000.0, 5),
            _edge("c", "b", 500.0, 2),
            _edge("b", "d", 1_200.0, 4),
        ]
        nodes = [_node(x) for x in ("a", "b", "c", "d")]

        fs = _assemble(nodes, edges)
        col = {name: i for i, name in enumerate(fs.feature_names)}
        b = fs.node_ids.index("b")

        assert fs.x[b, col["in_degree"]] == 2      # a and c
        assert fs.x[b, col["out_degree"]] == 1     # d
        assert fs.x[b, col["total_in_amount"]] == pytest.approx(1_500.0)
        assert fs.x[b, col["total_out_amount"]] == pytest.approx(1_200.0)
        assert fs.x[b, col["in_tx_count"]] == pytest.approx(7.0)
        assert fs.x[b, col["out_tx_count"]] == pytest.approx(4.0)
        assert fs.x[b, col["net_flow"]] == pytest.approx(300.0)

    def test_pass_through_mule_has_flow_ratio_near_half(self):
        """A mule forwards almost everything it receives."""
        edges = [_edge("victim", "mule", 10_000.0, 1), _edge("mule", "exit", 9_900.0, 1)]
        fs = _assemble([_node(x) for x in ("victim", "mule", "exit")], edges)
        col = {name: i for i, name in enumerate(fs.feature_names)}

        mule = fs.x[fs.node_ids.index("mule"), col["flow_ratio"]]
        assert mule == pytest.approx(9_900.0 / 19_900.0, abs=1e-6)

        # A pure sink forwards nothing, so ratio 0; a pure source, 1.
        assert fs.x[fs.node_ids.index("exit"), col["flow_ratio"]] == pytest.approx(0.0)
        assert fs.x[fs.node_ids.index("victim"), col["flow_ratio"]] == pytest.approx(1.0)

    def test_isolated_account_gets_zero_not_nan(self):
        """0/0 in flow_ratio must not leak a NaN into the matrix."""
        fs = _assemble([_node("lonely")], [])

        assert fs.num_nodes == 1
        assert np.isfinite(fs.x).all()
        assert fs.x[0, fs.feature_names.index("flow_ratio")] == pytest.approx(0.0)

        # Everything is zero except the is_account one-hot, which is legitimately set.
        one_hot = fs.feature_names.index("is_account")
        others = [i for i in range(fs.num_features) if i != one_hot]
        assert fs.x[0, others].sum() == pytest.approx(0.0)
        assert fs.x[0, one_hot] == pytest.approx(1.0)

    def test_pagerank_score_is_carried_through(self):
        fs = _assemble([_node("a", pagerank=0.42)], [])
        col = fs.feature_names.index("pagerank_score")
        assert fs.x[0, col] == pytest.approx(0.42)

    def test_missing_pagerank_becomes_zero(self):
        """Accounts predating the PageRank worker have no score."""
        fs = _assemble([_node("a", pagerank=None)], [])
        col = fs.feature_names.index("pagerank_score")
        assert fs.x[0, col] == pytest.approx(0.0)


class TestAccountAge:
    """Age comes from FLOWS_TO.first_ts, never Account.created_at.

    created_at is set to Neo4j timestamp() when the outbox inserts the node, so
    it records ingest time. On the real 2022 dataset ingested in 2026 that made
    every age negative and clipped the whole column to zero.
    """

    def test_age_computed_from_first_activity_in_seconds(self):
        first = int((REF - timedelta(days=30)).timestamp())
        fs = _assemble(
            [_node("a"), _node("b")],
            [_edge("a", "b", 100.0, 1, first_ts=first)],
        )
        col = fs.feature_names.index("account_age_days")
        assert fs.x[fs.node_ids.index("a"), col] == pytest.approx(30.0, abs=0.01)

    def test_ingest_time_created_at_is_ignored(self):
        """A created_at far in the future must not drive the feature."""
        first = int((REF - timedelta(days=10)).timestamp())
        ingested_later = int((REF + timedelta(days=1000)).timestamp() * 1000)
        fs = _assemble(
            [_node("a", created_at=ingested_later), _node("b", created_at=ingested_later)],
            [_edge("a", "b", 100.0, 1, first_ts=first)],
        )
        col = fs.feature_names.index("account_age_days")
        assert fs.x[fs.node_ids.index("a"), col] == pytest.approx(10.0, abs=0.01)

    def test_receiver_age_also_counted(self):
        """An account is as old as its first edge, sent or received."""
        first = int((REF - timedelta(days=7)).timestamp())
        fs = _assemble(
            [_node("a"), _node("b")],
            [_edge("a", "b", 100.0, 1, first_ts=first)],
        )
        col = fs.feature_names.index("account_age_days")
        assert fs.x[fs.node_ids.index("b"), col] == pytest.approx(7.0, abs=0.01)

    def test_earliest_edge_wins(self):
        old = int((REF - timedelta(days=40)).timestamp())
        recent = int((REF - timedelta(days=2)).timestamp())
        fs = _assemble(
            [_node("a"), _node("b"), _node("c")],
            [
                _edge("a", "b", 100.0, 1, first_ts=recent),
                _edge("c", "a", 50.0, 1, first_ts=old),
            ],
        )
        col = fs.feature_names.index("account_age_days")
        assert fs.x[fs.node_ids.index("a"), col] == pytest.approx(40.0, abs=0.01)

    def test_no_edges_gives_zero_not_epoch_zero(self):
        """An edgeless account must not read as created in 1970."""
        fs = _assemble([_node("a")], [])
        col = fs.feature_names.index("account_age_days")
        assert fs.x[0, col] == pytest.approx(0.0)

    def test_future_first_ts_clamps_to_zero(self):
        first = int((REF + timedelta(days=5)).timestamp())
        fs = _assemble(
            [_node("a"), _node("b")],
            [_edge("a", "b", 100.0, 1, first_ts=first)],
        )
        col = fs.feature_names.index("account_age_days")
        assert fs.x[0, col] == pytest.approx(0.0)

    def test_node_first_ts_exposed_for_chronological_split(self):
        """Metadata, not a feature — absolute time must not reach the model."""
        first = int((REF - timedelta(days=3)).timestamp())
        fs = _assemble(
            [_node("a"), _node("b")],
            [_edge("a", "b", 100.0, 1, first_ts=first)],
        )
        assert fs.node_first_ts is not None
        assert fs.node_first_ts[fs.node_ids.index("a")] == pytest.approx(first)
        assert "first_active_ts" not in fs.feature_names


class TestSelfLoops:
    """Self-transfers must not contaminate the counterparty aggregates.

    On the loaded HI-Small graph, 365,987 of 1,010,384 FLOWS_TO edges (36%) are
    self-loops, and 92,154 accounts (17.9%) have no other neighbour — every one
    of which would otherwise show the exact pass-through mule signature.
    """

    def test_self_loop_excluded_from_edge_index(self):
        fs = _assemble(
            [_node("a"), _node("b")],
            [_edge("a", "b", 100.0, 1), _edge("a", "a", 500.0, 5)],
        )
        assert fs.edge_index.shape == (2, 1)
        assert fs.node_ids[fs.edge_index[0, 0]] == "a"
        assert fs.node_ids[fs.edge_index[1, 0]] == "b"

    def test_self_loop_only_account_does_not_fake_a_mule(self):
        """The whole reason for this handling.

        Counting a self-loop as a counterparty yields out_degree=1, in_degree=1,
        net_flow=0 and flow_ratio=0.5 — indistinguishable from a perfect
        pass-through mule, from an account that never touched anyone.
        """
        fs = _assemble([_node("lonely")], [_edge("lonely", "lonely", 9_000.0, 9)])
        col = {name: i for i, name in enumerate(fs.feature_names)}

        assert fs.x[0, col["out_degree"]] == pytest.approx(0.0)
        assert fs.x[0, col["in_degree"]] == pytest.approx(0.0)
        assert fs.x[0, col["total_out_amount"]] == pytest.approx(0.0)
        assert fs.x[0, col["flow_ratio"]] == pytest.approx(0.0)
        assert fs.edge_index.shape == (2, 0)

    def test_self_loop_volume_is_preserved_not_discarded(self):
        fs = _assemble([_node("a")], [_edge("a", "a", 9_000.0, 9)])
        col = {name: i for i, name in enumerate(fs.feature_names)}

        assert fs.x[0, col["self_loop_count"]] == pytest.approx(9.0)
        assert fs.x[0, col["self_loop_amount"]] == pytest.approx(9_000.0)

    def test_real_counterparty_aggregates_unaffected(self):
        fs = _assemble(
            [_node("a"), _node("b")],
            [_edge("a", "b", 100.0, 2), _edge("a", "a", 500.0, 5)],
        )
        col = {name: i for i, name in enumerate(fs.feature_names)}
        a = fs.node_ids.index("a")

        assert fs.x[a, col["out_degree"]] == pytest.approx(1.0)
        assert fs.x[a, col["total_out_amount"]] == pytest.approx(100.0)
        assert fs.x[a, col["out_tx_count"]] == pytest.approx(2.0)
        assert fs.x[a, col["self_loop_amount"]] == pytest.approx(500.0)

    def test_self_loops_still_establish_account_age(self):
        """An account paying itself proves it existed at that time."""
        first = int((REF - timedelta(days=12)).timestamp())
        fs = _assemble([_node("a")], [_edge("a", "a", 100.0, 1, first_ts=first)])
        col = fs.feature_names.index("account_age_days")
        assert fs.x[0, col] == pytest.approx(12.0, abs=0.01)

    def test_can_be_disabled_for_comparison(self):
        builder = FeatureBuilder(neo4j_client=None)
        fs = builder._assemble(
            [_node("a")], [_edge("a", "a", 500.0, 5)], {}, [], (1, 24, 168), REF,
            False,
        )
        col = {name: i for i, name in enumerate(fs.feature_names)}

        assert fs.edge_index.shape == (2, 1)
        assert fs.x[0, col["out_degree"]] == pytest.approx(1.0)


class TestCommunityFeatures:
    def test_community_size_counts_members(self):
        nodes = [
            _node("a", community_id="ab12cd34ef56"),
            _node("b", community_id="ab12cd34ef56"),
            _node("c", community_id="ab12cd34ef56"),
            _node("d", community_id="ff00ff00ff00"),
        ]
        fs = _assemble(nodes, [])
        col = fs.feature_names.index("community_size")

        assert fs.x[fs.node_ids.index("a"), col] == pytest.approx(3.0)
        assert fs.x[fs.node_ids.index("d"), col] == pytest.approx(1.0)

    def test_uncommunitied_account_gets_zero_size(self):
        fs = _assemble([_node("a", community_id=None)], [])
        col = fs.feature_names.index("community_size")
        assert fs.x[0, col] == pytest.approx(0.0)

    def test_community_risk_read_from_flag_details(self):
        nodes = [_node("a", community_id="deadbeef1234")]
        flags = [
            {
                "flag_type": "COMMUNITY",
                "risk_level": "high",
                "risk_score": 0.785,
                "account_ids": ["a", "b", "c"],
                "details": {"community_id": "deadbeef1234", "flagged_member_count": 2},
            }
        ]
        fs = _assemble(nodes, [], flags=flags)
        risk = fs.feature_names.index("community_risk_score")
        flagged = fs.feature_names.index("community_flagged_members")

        assert fs.x[0, risk] == pytest.approx(0.785)
        # 2 flagged members, not 3 total members.
        assert fs.x[0, flagged] == pytest.approx(2.0)

    def test_flagged_members_does_not_duplicate_community_size(self):
        """flagged_member_count comes from details, not len(account_ids).

        Using the account_ids length would make column 12 a copy of column 10.
        """
        nodes = [
            _node("a", community_id="cafe12345678"),
            _node("b", community_id="cafe12345678"),
            _node("c", community_id="cafe12345678"),
        ]
        flags = [
            {
                "flag_type": "COMMUNITY",
                "risk_level": "medium",
                "risk_score": 0.5,
                "account_ids": ["a", "b", "c"],
                "details": {"community_id": "cafe12345678", "flagged_member_count": 1},
            }
        ]
        fs = _assemble(nodes, [], flags=flags)
        size = fs.x[0, fs.feature_names.index("community_size")]
        flagged = fs.x[0, fs.feature_names.index("community_flagged_members")]

        assert size == pytest.approx(3.0)
        assert flagged == pytest.approx(1.0)
        assert size != flagged

    def test_missing_flagged_member_count_is_zero(self):
        nodes = [_node("a", community_id="cafe12345678")]
        flags = [
            {
                "flag_type": "COMMUNITY",
                "risk_level": "medium",
                "risk_score": 0.5,
                "account_ids": ["a"],
                "details": {"community_id": "cafe12345678"},
            }
        ]
        fs = _assemble(nodes, [], flags=flags)
        assert fs.x[0, fs.feature_names.index("community_flagged_members")] == pytest.approx(0.0)

    def test_details_as_json_string_is_parsed(self):
        """asyncpg hands back JSONB as str unless a codec is registered."""
        nodes = [_node("a", community_id="deadbeef1234")]
        flags = [
            {
                "flag_type": "COMMUNITY",
                "risk_level": "high",
                "risk_score": 60.0,
                "account_ids": ["a"],
                "details": '{"community_id": "deadbeef1234"}',
            }
        ]
        fs = _assemble(nodes, [], flags=flags)
        assert fs.x[0, fs.feature_names.index("community_risk_score")] == pytest.approx(60.0)


class TestWeakLabels:
    def test_flagged_account_takes_its_risk_level(self):
        flags = [
            {
                "flag_type": "CYCLE",
                "risk_level": "critical",
                "risk_score": 95.0,
                "account_ids": ["a", "b"],
                "details": {},
            }
        ]
        fs = _assemble([_node("a"), _node("b"), _node("c")], [], flags=flags)

        assert fs.y[fs.node_ids.index("a")] == 3      # critical
        assert fs.y[fs.node_ids.index("b")] == 3
        assert fs.y[fs.node_ids.index("c")] == 0      # unflagged -> low

    def test_unflagged_accounts_are_masked_out(self):
        """labelled_mask separates 'looked and found nothing' from 'never looked'."""
        flags = [
            {
                "flag_type": "CYCLE",
                "risk_level": "medium",
                "risk_score": 50.0,
                "account_ids": ["a"],
                "details": {},
            }
        ]
        fs = _assemble([_node("a"), _node("b")], [], flags=flags)

        assert fs.labelled_mask[fs.node_ids.index("a")]
        assert not fs.labelled_mask[fs.node_ids.index("b")]
        assert fs.labelled_mask.sum() == 1

    def test_multiple_flags_take_the_highest_level(self):
        """A critical cycle must not be diluted by a medium one."""
        flags = [
            {
                "flag_type": "CYCLE",
                "risk_level": "medium",
                "risk_score": 40.0,
                "account_ids": ["a"],
                "details": {},
            },
            {
                "flag_type": "CYCLE",
                "risk_level": "critical",
                "risk_score": 99.0,
                "account_ids": ["a"],
                "details": {},
            },
        ]
        fs = _assemble([_node("a")], [], flags=flags)
        assert fs.y[0] == 3

    def test_community_flags_never_produce_labels(self):
        """The leakage fix: COMMUNITY risk_level must not become y.

        score_community derives risk_level from risk_score by threshold, and
        risk_score is feature column 11 — so labelling from COMMUNITY hands the
        model its own answer.
        """
        flags = [
            {
                "flag_type": "COMMUNITY",
                "risk_level": "critical",
                "risk_score": 0.95,
                "account_ids": ["a"],
                "details": {"community_id": "deadbeef1234"},
            }
        ]
        fs = _assemble([_node("a", community_id="deadbeef1234")], [], flags=flags)

        assert fs.y[0] == 0
        assert not fs.labelled_mask[0]
        # The community features still populate — Louvain stays a feature provider.
        assert fs.x[0, fs.feature_names.index("community_risk_score")] == pytest.approx(0.95)

    def test_community_flag_does_not_mask_a_cycle_label(self):
        """A CYCLE label still lands when a COMMUNITY flag is also present."""
        flags = [
            {
                "flag_type": "COMMUNITY",
                "risk_level": "critical",
                "risk_score": 0.95,
                "account_ids": ["a"],
                "details": {"community_id": "deadbeef1234"},
            },
            {
                "flag_type": "CYCLE",
                "risk_level": "high",
                "risk_score": 80.0,
                "account_ids": ["a"],
                "details": {},
            },
        ]
        fs = _assemble([_node("a", community_id="deadbeef1234")], [], flags=flags)

        assert fs.y[0] == 2          # from CYCLE, not the critical COMMUNITY flag
        assert fs.labelled_mask[0]

    def test_labels_stay_in_class_range(self):
        flags = [
            {
                "flag_type": "CYCLE",
                "risk_level": level,
                "risk_score": 10.0,
                "account_ids": [level],
                "details": {},
            }
            for level in ("low", "medium", "high", "critical")
        ]
        nodes = [_node(level) for level in ("low", "medium", "high", "critical")]
        fs = _assemble(nodes, [], flags=flags)

        assert fs.y.min() >= 0
        assert fs.y.max() < NUM_CLASSES
        assert sorted(fs.y.tolist()) == [0, 1, 2, 3]

    def test_unknown_risk_level_is_ignored(self):
        flags = [
            {
                "flag_type": "CYCLE",
                "risk_level": "catastrophic",  # not in the enum
                "risk_score": 1.0,
                "account_ids": ["a"],
                "details": {},
            }
        ]
        fs = _assemble([_node("a")], [], flags=flags)
        assert fs.y[0] == 0
        assert not fs.labelled_mask[0]

    def test_flag_for_unknown_account_does_not_crash(self):
        flags = [
            {
                "flag_type": "CYCLE",
                "risk_level": "high",
                "risk_score": 80.0,
                "account_ids": ["ghost"],
                "details": {},
            }
        ]
        fs = _assemble([_node("a")], [], flags=flags)
        assert fs.labelled_mask.sum() == 0


class TestEdgeIndex:
    def test_edge_index_uses_node_offsets_and_keeps_direction(self):
        edges = [_edge("a", "b", 100.0, 1)]
        fs = _assemble([_node("a"), _node("b")], edges)

        assert fs.edge_index.shape == (2, 1)
        assert fs.node_ids[fs.edge_index[0, 0]] == "a"
        assert fs.node_ids[fs.edge_index[1, 0]] == "b"
        assert fs.edge_weight[0] == pytest.approx(100.0)

    def test_edge_weight_is_total_amount(self):
        """CLAUDE.md: graph algorithms read rel.total_amount as edge weight."""
        edges = [_edge("a", "b", 4_242.0, 7)]
        fs = _assemble([_node("a"), _node("b")], edges)
        assert fs.edge_weight[0] == pytest.approx(4_242.0)

    def test_endpoint_missing_from_node_export_is_added(self):
        """A partially-synced graph must not silently lose edges."""
        edges = [_edge("a", "unsynced", 100.0, 1)]
        fs = _assemble([_node("a")], edges)

        assert "unsynced" in fs.node_ids
        assert fs.edge_index.shape == (2, 1)
        assert fs.num_nodes == 2

    def test_no_edges_gives_well_shaped_empty_arrays(self):
        fs = _assemble([_node("a")], [])
        assert fs.edge_index.shape == (2, 0)
        assert fs.edge_weight.shape == (0,)


class TestFeatureMatrixContract:
    def test_feature_names_match_matrix_width(self):
        fs = _assemble([_node("a")], [])
        assert fs.x.shape == (1, len(fs.feature_names))
        assert fs.num_features == len(fs.feature_names)

    def test_expected_feature_blocks_are_present(self):
        fs = _assemble([_node("a")], [])
        for name in GRAPH_FEATURES + COMMUNITY_FEATURES + NODE_TYPE_FEATURES:
            assert name in fs.feature_names

        # 3 windows x 4 metrics
        for label in ("1h", "24h", "7d"):
            for prefix in ("volume_out", "volume_in", "txn_out", "txn_in"):
                assert f"{prefix}_{label}" in fs.feature_names

    def test_feature_names_have_no_duplicates(self):
        """Duplicate names would make column lookups silently ambiguous."""
        fs = _assemble([_node("a")], [])
        assert len(fs.feature_names) == len(set(fs.feature_names))

    def test_matrix_is_float32_and_labels_int64(self):
        fs = _assemble([_node("a")], [])
        assert fs.x.dtype == np.float32
        assert fs.y.dtype == np.int64
        assert fs.labelled_mask.dtype == np.bool_
        assert fs.edge_index.dtype == np.int64

    def test_windows_configuration_changes_feature_width(self):
        narrow = _assemble([_node("a")], [], windows_hours=(24,))
        wide = _assemble([_node("a")], [], windows_hours=(1, 24, 168))
        assert wide.num_features == narrow.num_features + 8  # 2 extra windows x 4

    def test_node_type_one_hot_is_set(self):
        fs = _assemble([_node("a", labels=["Account"])], [])
        assert fs.x[0, fs.feature_names.index("is_account")] == pytest.approx(1.0)
        assert fs.x[0, fs.feature_names.index("is_bank")] == pytest.approx(0.0)

    def test_empty_graph_keeps_feature_width(self):
        """Zero nodes is valid; a zero-width matrix is not."""
        fs = _assemble([], [])
        assert fs.num_nodes == 0
        assert fs.x.shape == (0, len(fs.feature_names))
        assert len(fs.feature_names) > 0


class TestRedisVolumeFeatures:
    def test_volumes_land_in_the_right_direction_and_window(self):
        volumes = {
            "a": {
                "volume_out_1h": 100.0,
                "volume_in_1h": 0.0,
                "txn_out_1h": 1.0,
                "txn_in_1h": 0.0,
                "volume_out_24h": 500.0,
                "volume_in_24h": 20.0,
                "txn_out_24h": 3.0,
                "txn_in_24h": 1.0,
                "volume_out_7d": 900.0,
                "volume_in_7d": 60.0,
                "txn_out_7d": 8.0,
                "txn_in_7d": 2.0,
            }
        }
        fs = _assemble([_node("a")], [], volumes=volumes)
        col = {name: i for i, name in enumerate(fs.feature_names)}

        assert fs.x[0, col["volume_out_1h"]] == pytest.approx(100.0)
        assert fs.x[0, col["volume_in_24h"]] == pytest.approx(20.0)
        assert fs.x[0, col["txn_out_7d"]] == pytest.approx(8.0)

    def test_account_absent_from_redis_is_zero_filled(self):
        fs = _assemble([_node("a"), _node("b")], [], volumes={"a": {"volume_out_1h": 5.0}})
        col = fs.feature_names.index("volume_out_1h")

        assert fs.x[fs.node_ids.index("a"), col] == pytest.approx(5.0)
        assert fs.x[fs.node_ids.index("b"), col] == pytest.approx(0.0)
        assert np.isfinite(fs.x).all()


# --------------------------------------------------------------------------
# Bulk Redis reader
# --------------------------------------------------------------------------


class FakePipeline:
    """Mimics redis.asyncio pipeline: queue reads, execute returns in order."""

    def __init__(self, store: Dict[str, List[tuple]]):
        self.store = store
        self.queued: List[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def zrangebyscore(self, key, min, max):
        self.queued.append((key, min, max))

    async def execute(self):
        results = []
        for key, lo, hi in self.queued:
            members = [m for m, score in self.store.get(key, []) if lo <= score <= hi]
            results.append(members)
        self.queued = []
        return results


class FakeRedis:
    """Minimal SCAN + pipeline stand-in, returning all keys in one batch."""

    def __init__(self, store: Dict[str, List[tuple]]):
        self.store = store
        self.scan_calls = 0

    async def scan(self, cursor, match=None, count=None):
        self.scan_calls += 1
        return 0, list(self.store.keys())

    def pipeline(self, transaction=False):
        return FakePipeline(self.store)


def _member(amount_cents: int, ts: int) -> tuple:
    """ZSET member as add_edge_to_timeseries writes it: 'amount|ts', score=ts."""
    return (f"{amount_cents}|{ts}", ts)


@pytest.mark.asyncio
class TestBulkAccountVolumes:
    async def test_buckets_members_into_windows(self):
        now = int(REF.timestamp())
        store = {
            "edge:a:b": [
                _member(100, now - 600),          # 10 min ago -> in 1h, 24h, 7d
                _member(200, now - 6 * 3600),     # 6h ago     -> in 24h, 7d
                _member(400, now - 3 * 86400),    # 3d ago     -> in 7d only
            ]
        }
        client = RedisClient()
        client.client = FakeRedis(store)

        volumes = await client.get_all_account_volumes(reference_time=REF)

        a = volumes["a"]
        assert a["volume_out_1h"] == pytest.approx(100.0)
        assert a["volume_out_24h"] == pytest.approx(300.0)
        assert a["volume_out_7d"] == pytest.approx(700.0)
        assert a["txn_out_1h"] == pytest.approx(1.0)
        assert a["txn_out_24h"] == pytest.approx(2.0)
        assert a["txn_out_7d"] == pytest.approx(3.0)

    async def test_sender_gets_out_and_receiver_gets_in(self):
        now = int(REF.timestamp())
        store = {"edge:payer:payee": [_member(5_000, now - 60)]}
        client = RedisClient()
        client.client = FakeRedis(store)

        volumes = await client.get_all_account_volumes(reference_time=REF)

        assert volumes["payer"]["volume_out_1h"] == pytest.approx(5_000.0)
        assert volumes["payer"]["volume_in_1h"] == pytest.approx(0.0)
        assert volumes["payee"]["volume_in_1h"] == pytest.approx(5_000.0)
        assert volumes["payee"]["volume_out_1h"] == pytest.approx(0.0)

    async def test_scans_keyspace_once_regardless_of_account_count(self):
        """The whole reason this method exists instead of get_account_in_degree."""
        now = int(REF.timestamp())
        store = {
            f"edge:sender{i}:receiver{i}": [_member(10, now - 60)] for i in range(25)
        }
        client = RedisClient()
        fake = FakeRedis(store)
        client.client = fake

        volumes = await client.get_all_account_volumes(reference_time=REF)

        assert len(volumes) == 50       # 25 senders + 25 receivers
        assert fake.scan_calls == 1     # not one scan per account

    async def test_uses_utc_aware_now(self):
        """The old path used datetime.utcnow().timestamp(), skewing windows by
        the host UTC offset — enough to make a 1h window match nothing."""
        now = int(REF.timestamp())
        store = {"edge:a:b": [_member(100, now - 60)]}  # 1 min before REF
        client = RedisClient()
        client.client = FakeRedis(store)

        volumes = await client.get_all_account_volumes(reference_time=REF)

        # A tz-naive reading of REF would shift the window off this member.
        assert volumes["a"]["volume_out_1h"] == pytest.approx(100.0)

    async def test_malformed_member_is_skipped(self):
        now = int(REF.timestamp())
        store = {
            "edge:a:b": [
                ("not-a-number|abc", now - 60),
                ("no-delimiter", now - 60),
                _member(300, now - 60),
            ]
        }
        client = RedisClient()
        client.client = FakeRedis(store)

        volumes = await client.get_all_account_volumes(reference_time=REF)
        assert volumes["a"]["volume_out_1h"] == pytest.approx(300.0)

    async def test_non_edge_keys_are_ignored(self):
        now = int(REF.timestamp())
        store = {
            "edge:a:b": [_member(100, now - 60)],
            "cache:something": [("junk", now)],
            "edge:malformed": [("junk", now)],
        }
        client = RedisClient()
        client.client = FakeRedis(store)

        volumes = await client.get_all_account_volumes(reference_time=REF)
        assert set(volumes) == {"a", "b"}

    async def test_empty_windows_rejected(self):
        client = RedisClient()
        client.client = FakeRedis({})
        with pytest.raises(ValueError):
            await client.get_all_account_volumes(windows_hours=())


# --------------------------------------------------------------------------
# PyG conversion
# --------------------------------------------------------------------------


class TestToPyG:
    def test_converts_to_data_object(self):
        pytest.importorskip("torch_geometric")
        import torch

        edges = [_edge("a", "b", 1_000.0, 3), _edge("b", "c", 900.0, 2)]
        nodes = [_node("a"), _node("b"), _node("c")]
        flags = [
            {
                "flag_type": "CYCLE",
                "risk_level": "high",
                "risk_score": 80.0,
                "account_ids": ["b"],
                "details": {},
            }
        ]
        data = _assemble(nodes, edges, flags=flags).to_pyg()

        assert data.x.shape[0] == 3
        assert data.edge_index.shape == (2, 2)
        assert data.y.shape == (3,)
        assert data.x.dtype == torch.float32
        assert data.y.dtype == torch.int64
        assert data.node_ids == ["a", "b", "c"]
        data.validate(raise_on_error=True)

    def test_sageconv_consumes_the_real_feature_set(self):
        """The point of the whole module: these tensors must feed the model."""
        pytest.importorskip("torch_geometric")
        import torch
        from torch_geometric.nn import SAGEConv

        edges = [_edge("a", "b", 1_000.0, 3), _edge("b", "c", 900.0, 2)]
        fs = _assemble([_node("a"), _node("b"), _node("c")], edges)
        data = fs.to_pyg()

        torch.manual_seed(0)
        conv = SAGEConv(fs.num_features, 8)
        with torch.no_grad():
            out = conv(data.x, data.edge_index)

        assert out.shape == (3, 8)
        assert torch.isfinite(out).all()
