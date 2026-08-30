"""
Tests for the training-readiness audit (ml/readiness.py).

The audit is what decides whether training can start, so a false PASS is worse
than no audit at all. These pin each check's pass/warn/fail boundary.
"""

from typing import List, Optional

import numpy as np
import pytest

from ml.evaluate import GroundTruth
from ml.features import FeatureSet
from ml.readiness import (
    FAIL,
    PASS,
    WARN,
    Report,
    check_dry_run,
    check_integrity,
    check_labels,
    check_scale,
    check_structure,
    check_time_split,
    check_volume,
)


def _feature_set(
    x: np.ndarray,
    edge_index: Optional[np.ndarray] = None,
    node_ids: Optional[List[str]] = None,
    y: Optional[np.ndarray] = None,
    labelled_mask: Optional[np.ndarray] = None,
    feature_names: Optional[List[str]] = None,
) -> FeatureSet:
    num_nodes = x.shape[0]
    if edge_index is None:
        edge_index = np.zeros((2, 0), dtype=np.int64)
    return FeatureSet(
        node_ids=node_ids or [f"n{i}" for i in range(num_nodes)],
        x=x.astype(np.float32),
        y=y if y is not None else np.zeros(num_nodes, dtype=np.int64),
        labelled_mask=(
            labelled_mask
            if labelled_mask is not None
            else np.zeros(num_nodes, dtype=bool)
        ),
        edge_index=edge_index,
        edge_weight=np.ones(edge_index.shape[1], dtype=np.float32),
        feature_names=feature_names or [f"f{i}" for i in range(x.shape[1])],
    )


def _status(report: Report, name: str) -> str:
    for check in report.checks:
        if check.name == name:
            return check.status
    raise AssertionError(f"no check named {name!r}")


class TestVolume:
    def test_empty_graph_fails(self):
        report = Report()
        check_volume(report, _feature_set(np.zeros((0, 3))), {})
        assert _status(report, "volume") == FAIL

    def test_nodes_without_edges_fails(self):
        report = Report()
        check_volume(report, _feature_set(np.ones((100, 3))), {})
        assert _status(report, "volume") == FAIL

    def test_tiny_graph_warns(self):
        report = Report()
        edges = np.array([[0], [1]], dtype=np.int64)
        check_volume(report, _feature_set(np.ones((10, 3)), edges), {})
        assert _status(report, "volume") == WARN

    def test_large_graph_passes(self):
        report = Report()
        n = 5_000
        edges = np.vstack([np.arange(n - 1), np.arange(1, n)]).astype(np.int64)
        check_volume(report, _feature_set(np.ones((n, 3)), edges), {})
        assert _status(report, "volume") == PASS


class TestIntegrity:
    def test_nan_fails(self):
        x = np.ones((5, 3))
        x[2, 1] = np.nan
        report = Report()
        check_integrity(report, _feature_set(x))
        assert _status(report, "integrity") == FAIL

    def test_inf_fails(self):
        x = np.ones((5, 3))
        x[0, 0] = np.inf
        report = Report()
        check_integrity(report, _feature_set(x))
        assert _status(report, "integrity") == FAIL

    def test_all_zero_column_warns(self):
        x = np.array([[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
        report = Report()
        check_integrity(report, _feature_set(x))
        assert _status(report, "integrity") == WARN

    def test_constant_nonzero_column_also_warns(self):
        """A column stuck at 7 teaches nothing, just like one stuck at 0."""
        x = np.array([[1.0, 7.0], [2.0, 7.0], [3.0, 7.0]])
        report = Report()
        check_integrity(report, _feature_set(x, feature_names=["varies", "stuck"]))

        assert _status(report, "integrity") == WARN
        detail = " ".join(report.checks[0].lines)
        assert "constant: stuck" in detail

    def test_all_informative_passes(self):
        x = np.array([[1.0, 4.0], [2.0, 5.0], [3.0, 7.0]])
        report = Report()
        check_integrity(report, _feature_set(x))
        assert _status(report, "integrity") == PASS


class TestScale:
    def test_wide_spread_warns(self):
        """The real matrix has amounts at 1e14 next to pagerank at 1e-4."""
        x = np.array([[1e14, 0.5], [2e14, 0.4]])
        report = Report()
        check_scale(report, _feature_set(x, feature_names=["amount", "ratio"]))

        assert _status(report, "scale") == WARN
        detail = " ".join(report.checks[0].lines)
        assert "amount" in detail

    def test_comparable_scales_pass(self):
        x = np.array([[1.0, 0.5], [2.0, 0.9], [3.0, 0.1]])
        report = Report()
        check_scale(report, _feature_set(x))
        assert _status(report, "scale") == PASS

    def test_float32_precision_note_appears(self):
        x = np.array([[1e9, 1e8], [2e9, 2e8]])
        report = Report()
        check_scale(report, _feature_set(x, feature_names=["big", "also_big"]))
        assert any("float32" in line for line in report.checks[0].lines)

    def test_all_zero_matrix_fails(self):
        report = Report()
        check_scale(report, _feature_set(np.zeros((4, 3))))
        assert _status(report, "scale") == FAIL


class TestStructure:
    def test_duplicate_directed_pair_fails(self):
        """FLOWS_TO is MERGEd per pair, so duplicates mean a split aggregate."""
        edges = np.array([[0, 0], [1, 1]], dtype=np.int64)
        report = Report()
        check_structure(report, _feature_set(np.ones((3, 2)), edges))
        assert _status(report, "structure") == FAIL

    def test_self_loop_warns(self):
        edges = np.array([[0, 1], [1, 1]], dtype=np.int64)
        report = Report()
        check_structure(report, _feature_set(np.ones((3, 2)), edges))
        assert _status(report, "structure") == WARN

    def test_self_loop_only_node_counted_as_unreachable(self):
        """A self-loop gives in-degree 1 AND out-degree 1.

        Counting raw degree therefore calls a node that only pays itself
        "connected", when message passing can reach nothing from it. Node 2 here
        has only a self-loop.
        """
        edges = np.array([[0, 2], [1, 2]], dtype=np.int64)
        report = Report()
        check_structure(report, _feature_set(np.ones((3, 2)), edges))

        assert _status(report, "structure") == WARN
        detail = " ".join(report.checks[0].lines)
        assert "no non-self neighbour: 1" in detail

    def test_no_self_loops_does_not_advise_dropping_them(self):
        """Advice must match what the graph actually has.

        After the feature builder started excluding self-loops, the check kept
        telling the reader to drop self-loops that were no longer there.
        """
        # 4 nodes: 0->1 real, node 3 is genuinely isolated. No self-loops.
        edges = np.array([[0, 1], [1, 2]], dtype=np.int64)
        report = Report()
        check_structure(report, _feature_set(np.ones((4, 2)), edges))

        assert _status(report, "structure") == WARN
        joined = " ".join(report.checks[0].lines + [report.checks[0].headline])
        assert "no self-loops" in joined
        assert "drop the self-loop edges" not in joined
        assert "only ever transacted with themselves" in joined

    def test_mostly_unreachable_fails(self):
        # 10 nodes, one real edge — 8 nodes touch nothing at all.
        edges = np.array([[0], [1]], dtype=np.int64)
        report = Report()
        check_structure(report, _feature_set(np.ones((10, 2)), edges))

        assert _status(report, "structure") == FAIL
        assert any("non-self neighbour" in line for line in report.checks[0].lines)

    def test_connected_graph_passes(self):
        n = 10
        edges = np.vstack([np.arange(n - 1), np.arange(1, n)]).astype(np.int64)
        report = Report()
        check_structure(report, _feature_set(np.ones((n, 2)), edges))
        assert _status(report, "structure") == PASS


class TestLabels:
    def test_no_labels_at_all_fails(self):
        report = Report()
        result = check_labels(report, _feature_set(np.ones((5, 2))), None)
        assert _status(report, "labels") == FAIL
        assert result["truth_mask"] is None

    def test_ground_truth_absent_from_graph_fails(self):
        report = Report()
        feature_set = _feature_set(np.ones((3, 2)), node_ids=["x", "y", "z"])
        ground_truth = GroundTruth(accounts_by_typology={"CYCLE": {"nowhere"}})

        check_labels(report, feature_set, ground_truth)
        assert _status(report, "labels") == FAIL

    def test_weak_labels_dwarfed_by_ground_truth_warns(self):
        """The live situation: 124 weak labels vs 3,170 ground-truth positives."""
        num_nodes = 100
        labelled = np.zeros(num_nodes, dtype=bool)
        labelled[0] = True
        feature_set = _feature_set(np.ones((num_nodes, 2)), labelled_mask=labelled)
        ground_truth = GroundTruth(
            accounts_by_typology={"CYCLE": {f"n{i}" for i in range(40)}}
        )

        report = Report()
        check_labels(report, feature_set, ground_truth)

        assert _status(report, "labels") == WARN
        assert any("train on ground truth" in c.headline for c in report.checks)

    def test_comparable_label_counts_pass(self):
        num_nodes = 100
        labelled = np.zeros(num_nodes, dtype=bool)
        labelled[:30] = True
        feature_set = _feature_set(np.ones((num_nodes, 2)), labelled_mask=labelled)
        ground_truth = GroundTruth(
            accounts_by_typology={"CYCLE": {f"n{i}" for i in range(20)}}
        )

        report = Report()
        check_labels(report, feature_set, ground_truth)
        assert _status(report, "labels") == PASS

    def test_reports_isolated_positives(self):
        """A laundering account with no edges cannot benefit from message passing."""
        # 5 nodes, one edge 0->1. n3 is a positive with no edges at all.
        edges = np.array([[0], [1]], dtype=np.int64)
        feature_set = _feature_set(np.ones((5, 2)), edges)
        ground_truth = GroundTruth(accounts_by_typology={"CYCLE": {"n1", "n3"}})

        report = Report()
        check_labels(report, feature_set, ground_truth)

        detail = " ".join(report.checks[0].lines)
        assert "positive degree" in detail
        assert "1 isolated" in detail        # n3
        assert "50.0%" in detail

    def test_reports_weak_label_agreement(self):
        """Stale flags from an earlier graph would show near-zero agreement."""
        labelled = np.zeros(10, dtype=bool)
        labelled[0] = True
        labelled[1] = True
        feature_set = _feature_set(np.ones((10, 2)), labelled_mask=labelled)
        # Only n0 is truly laundering, so agreement should read 1 of 2 = 50%.
        ground_truth = GroundTruth(accounts_by_typology={"CYCLE": {"n0"}})

        report = Report()
        check_labels(report, feature_set, ground_truth)

        detail = " ".join(report.checks[0].lines)
        assert "weak-label agreement : 1 of 2" in detail
        assert "50.0%" in detail


class TestTimeSplit:
    def test_no_timestamps_fails(self):
        report = Report()
        check_time_split(report, _feature_set(np.ones((5, 2))), None, None)
        assert _status(report, "time split") == FAIL

    def test_empty_timestamp_map_fails(self):
        report = Report()
        check_time_split(report, _feature_set(np.ones((5, 2))), {}, None)
        assert _status(report, "time split") == FAIL

    def test_split_with_positives_on_both_sides_still_warns_about_cumulative_features(self):
        """Positives on both sides is necessary but not sufficient.

        FLOWS_TO aggregates are incremented on MERGE and span the whole dataset,
        so the split separates accounts by inception without separating the
        feature values in time. Reporting PASS would imply a sound temporal
        setup that does not exist.
        """
        num_nodes = 400
        node_ids = [f"n{i}" for i in range(num_nodes)]
        times = {node_id: 1_600_000_000 + i * 3600 for i, node_id in enumerate(node_ids)}
        truth = np.zeros(num_nodes, dtype=bool)
        truth[:100] = True
        truth[-100:] = True

        report = Report()
        check_time_split(
            report, _feature_set(np.ones((num_nodes, 2)), node_ids=node_ids), times, truth
        )

        assert _status(report, "time split") == WARN
        joined = " ".join(report.checks[0].lines + [report.checks[0].headline])
        assert "cumulative" in joined
        assert any("TRANSFER.ts" in line for line in report.checks[0].lines)

    def test_too_few_positives_on_one_side_warns(self):
        num_nodes = 200
        node_ids = [f"n{i}" for i in range(num_nodes)]
        times = {node_id: 1_600_000_000 + i * 3600 for i, node_id in enumerate(node_ids)}
        truth = np.zeros(num_nodes, dtype=bool)
        truth[:80] = True          # all in the train side only

        report = Report()
        check_time_split(
            report, _feature_set(np.ones((num_nodes, 2)), node_ids=node_ids), times, truth
        )
        assert _status(report, "time split") == WARN

    def test_no_ground_truth_warns_but_still_splits(self):
        node_ids = [f"n{i}" for i in range(50)]
        times = {node_id: 1_600_000_000 + i * 60 for i, node_id in enumerate(node_ids)}

        report = Report()
        check_time_split(
            report, _feature_set(np.ones((50, 2)), node_ids=node_ids), times, None
        )
        assert _status(report, "time split") == WARN


class TestDryRun:
    def test_scale_gap_is_measured_not_assumed(self):
        """Raw vs standardized gradients on a wildly-scaled matrix.

        This is the evidence behind the scale warning: same graph, same weights,
        only the feature scaling differs.
        """
        pytest.importorskip("torch_geometric")

        x = np.array([[1e14, 0.5], [2e14, 0.4], [3e13, 0.9], [5e13, 0.2]])
        edges = np.array([[0, 1, 2], [1, 2, 3]], dtype=np.int64)
        truth = np.array([False, True, False, True])

        report = Report()
        check_dry_run(report, _feature_set(x, edges), truth)

        assert _status(report, "dry run") == WARN
        joined = " ".join(report.checks[0].lines)
        assert "raw features" in joined
        assert "standardized" in joined

    def test_well_scaled_features_pass(self):
        pytest.importorskip("torch_geometric")

        rng = np.random.default_rng(0)
        n = 60
        x = rng.normal(0.0, 1.0, size=(n, 5))
        edges = np.vstack([np.arange(n - 1), np.arange(1, n)]).astype(np.int64)
        truth = np.zeros(n, dtype=bool)
        truth[:6] = True

        report = Report()
        check_dry_run(report, _feature_set(x, edges), truth)
        assert _status(report, "dry run") == PASS

    def test_falls_back_to_weak_labels_without_ground_truth(self):
        pytest.importorskip("torch_geometric")

        rng = np.random.default_rng(0)
        n = 40
        x = rng.normal(size=(n, 4))
        edges = np.vstack([np.arange(n - 1), np.arange(1, n)]).astype(np.int64)
        y = np.zeros(n, dtype=np.int64)
        y[:4] = 3

        report = Report()
        check_dry_run(report, _feature_set(x, edges, y=y), None)

        assert any(
            "weak labels" in line for line in report.checks[0].lines
        )


class TestReportVerdict:
    def test_any_fail_makes_it_not_ready(self):
        report = Report()
        report.add("a", PASS, "fine")
        report.add("b", FAIL, "broken")
        assert "NOT READY" in report.render()
        assert "BLOCKER: b" in report.render()

    def test_warnings_are_ready_with_caveats(self):
        report = Report()
        report.add("a", PASS, "fine")
        report.add("b", WARN, "watch out")
        rendered = report.render()
        assert "READY, with 1 caveat" in rendered
        assert "CAVEAT: b" in rendered

    def test_all_pass_is_ready(self):
        report = Report()
        report.add("a", PASS, "fine")
        rendered = report.render()
        assert "VERDICT: READY" in rendered
        assert "caveat" not in rendered
