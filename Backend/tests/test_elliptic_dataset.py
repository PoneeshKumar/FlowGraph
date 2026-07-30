"""
Tests for the Elliptic Bitcoin loader.

Built against synthetic fixtures matching the real file layout, so they run
without the 200MB download. The real-data tests skip when it is absent.
"""

import csv
from pathlib import Path
from typing import List

import numpy as np
import pytest

from ml.datasets.elliptic import (
    CLASSES_FILE,
    DEFAULT_ELLIPTIC_DIR,
    EDGELIST_FILE,
    FEATURES_FILE,
    ILLICIT_CLASS,
    LICIT_CLASS,
    NUM_AGGREGATE_FEATURES,
    NUM_LOCAL_FEATURES,
    load_elliptic,
    time_step_split,
)


REAL_DIR = Path(DEFAULT_ELLIPTIC_DIR)
TOTAL_FEATURE_COLS = 1 + NUM_LOCAL_FEATURES + NUM_AGGREGATE_FEATURES  # 166


def _feature_row(tx_id: str, step: int, fill: float = 0.5) -> List[str]:
    """One row of elliptic_txs_features.csv: txId, time_step, then 165 floats."""
    return [tx_id, str(step)] + [str(fill)] * (TOTAL_FEATURE_COLS - 1)


def _write_fixture(
    directory: Path,
    nodes: List[tuple],          # (tx_id, time_step, class_code)
    edges: List[tuple],          # (src_id, dst_id)
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)

    with open(directory / FEATURES_FILE, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for tx_id, step, _ in nodes:
            writer.writerow(_feature_row(tx_id, step))

    with open(directory / CLASSES_FILE, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["txId", "class"])       # the real file has a header
        for tx_id, _, code in nodes:
            writer.writerow([tx_id, code])

    with open(directory / EDGELIST_FILE, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["txId1", "txId2"])      # the real file has a header
        writer.writerows(edges)

    return directory


@pytest.fixture
def fixture_dir(tmp_path) -> Path:
    return _write_fixture(
        tmp_path / "elliptic",
        nodes=[
            ("t1", 1, "1"),           # illicit
            ("t2", 1, "2"),           # licit
            ("t3", 2, "unknown"),
            ("t4", 40, "1"),          # illicit, later time step
            ("t5", 40, "2"),          # licit, later time step
        ],
        edges=[("t1", "t2"), ("t2", "t3"), ("t4", "t5")],
    )


class TestLabelMapping:
    def test_illicit_is_class_1_in_elliptic_not_2(self):
        """Elliptic inverts the intuitive coding: '1' illicit, '2' licit."""
        directory = _write_fixture(
            Path(pytest.importorskip("tempfile").mkdtemp()) / "e",
            nodes=[("a", 1, "1"), ("b", 1, "2")],
            edges=[("a", "b")],
        )
        fs = load_elliptic(directory)

        a = fs.node_ids.index("a")
        b = fs.node_ids.index("b")
        assert fs.y[a] == ILLICIT_CLASS      # critical
        assert fs.y[b] == LICIT_CLASS        # low

    def test_unknown_nodes_are_unlabelled_not_licit(self, fixture_dir):
        """77% of Elliptic is unknown — folding it into 'licit' would teach the
        model that most of the graph is provably clean."""
        fs = load_elliptic(fixture_dir)

        t3 = fs.node_ids.index("t3")
        assert not fs.labelled_mask[t3]
        # y defaults to the low class, but the mask is what training must use.
        assert fs.labelled_mask.sum() == 4

    def test_labelled_mask_marks_only_real_labels(self, fixture_dir):
        fs = load_elliptic(fixture_dir)
        labelled_ids = {fs.node_ids[i] for i in np.flatnonzero(fs.labelled_mask)}
        assert labelled_ids == {"t1", "t2", "t4", "t5"}


class TestGraphStructure:
    def test_produces_a_valid_feature_set(self, fixture_dir):
        fs = load_elliptic(fixture_dir)

        assert fs.num_nodes == 5
        # 166 columns: time_step + 93 local + 72 aggregate, as published.
        assert fs.x.shape == (5, TOTAL_FEATURE_COLS)
        assert fs.y.shape == (5,)
        assert fs.edge_index.shape == (2, 3)
        assert fs.x.dtype == np.float32
        assert fs.y.dtype == np.int64

    def test_edge_index_maps_ids_to_offsets(self, fixture_dir):
        fs = load_elliptic(fixture_dir)

        first_src = fs.node_ids[fs.edge_index[0, 0]]
        first_dst = fs.node_ids[fs.edge_index[1, 0]]
        assert (first_src, first_dst) == ("t1", "t2")

    def test_edge_weights_are_uniform(self, fixture_dir):
        """Elliptic carries no amounts, unlike FLOWS_TO."""
        fs = load_elliptic(fixture_dir)
        assert fs.edge_weight.shape == (3,)
        assert np.allclose(fs.edge_weight, 1.0)

    def test_feature_names_match_matrix_width(self, fixture_dir):
        fs = load_elliptic(fixture_dir)
        assert len(fs.feature_names) == fs.num_features
        assert fs.feature_names[0] == "time_step"
        assert "local_1" in fs.feature_names
        assert "aggregate_1" in fs.feature_names

    def test_headers_in_edge_and_class_files_are_skipped(self, fixture_dir):
        """The published CSVs have headers; the features file does not."""
        fs = load_elliptic(fixture_dir)
        assert "txId1" not in fs.node_ids
        assert fs.num_nodes == 5


class TestFiltering:
    def test_include_unknown_false_drops_them(self, fixture_dir):
        fs = load_elliptic(fixture_dir, include_unknown=False)

        assert fs.num_nodes == 4
        assert "t3" not in fs.node_ids
        assert fs.labelled_mask.all()

    def test_dropping_a_node_drops_its_edges(self, fixture_dir):
        """t2->t3 must disappear when t3 is filtered, not dangle."""
        fs = load_elliptic(fixture_dir, include_unknown=False)

        assert fs.edge_index.shape == (2, 2)
        assert fs.edge_index.max() < fs.num_nodes

    def test_time_step_filter(self, fixture_dir):
        fs = load_elliptic(fixture_dir, time_steps=(1, 2))

        assert set(fs.node_ids) == {"t1", "t2", "t3"}
        # The t4->t5 edge is outside the window and must be gone.
        assert fs.edge_index.shape == (2, 2)

    def test_empty_result_raises(self, fixture_dir):
        with pytest.raises(ValueError, match="No Elliptic nodes"):
            load_elliptic(fixture_dir, time_steps=(900, 999))

    def test_missing_files_raise_with_download_hint(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Kaggle"):
            load_elliptic(tmp_path / "absent")


class TestTimeStepSplit:
    def test_splits_on_time_not_randomly(self, fixture_dir):
        fs = load_elliptic(fixture_dir)
        train_mask, test_mask = time_step_split(fs, train_last_step=34)

        train_ids = {fs.node_ids[i] for i in np.flatnonzero(train_mask)}
        test_ids = {fs.node_ids[i] for i in np.flatnonzero(test_mask)}

        assert train_ids == {"t1", "t2"}      # steps 1-2, labelled
        assert test_ids == {"t4", "t5"}       # step 40, labelled

    def test_unlabelled_nodes_are_in_neither_split(self, fixture_dir):
        fs = load_elliptic(fixture_dir)
        train_mask, test_mask = time_step_split(fs)

        t3 = fs.node_ids.index("t3")
        assert not train_mask[t3]
        assert not test_mask[t3]

    def test_splits_do_not_overlap(self, fixture_dir):
        fs = load_elliptic(fixture_dir)
        train_mask, test_mask = time_step_split(fs)
        assert not (train_mask & test_mask).any()

    def test_missing_time_step_column_raises(self, fixture_dir):
        fs = load_elliptic(fixture_dir)
        fs.feature_names = ["something_else"] * fs.num_features
        with pytest.raises(ValueError, match="time_step"):
            time_step_split(fs)


class TestModelIntegration:
    def test_feature_set_feeds_sageconv(self, fixture_dir):
        """Whole point of emitting a FeatureSet: the same model consumes it."""
        pytest.importorskip("torch_geometric")
        import torch
        from torch_geometric.nn import SAGEConv

        fs = load_elliptic(fixture_dir)
        data = fs.to_pyg()

        torch.manual_seed(0)
        conv = SAGEConv(fs.num_features, 8)
        with torch.no_grad():
            out = conv(data.x, data.edge_index)

        assert out.shape == (fs.num_nodes, 8)
        assert torch.isfinite(out).all()

    def test_focal_loss_accepts_the_labels(self, fixture_dir):
        pytest.importorskip("torch")
        import torch

        from ml.losses import FocalLoss

        fs = load_elliptic(fixture_dir)
        logits = torch.randn(fs.num_nodes, 4)
        # Mask unlabelled nodes out, as training should.
        target = torch.from_numpy(fs.y).clone()
        target[~torch.from_numpy(fs.labelled_mask)] = -100

        loss = FocalLoss()(logits, target)
        assert torch.isfinite(loss)


@pytest.mark.skipif(
    not (REAL_DIR / FEATURES_FILE).exists(),
    reason="Elliptic dataset not downloaded to benchmarks/data/elliptic/",
)
class TestAgainstRealElliptic:
    """Runs only once the Kaggle dataset is present."""

    def test_published_shape(self):
        fs = load_elliptic(REAL_DIR)

        assert fs.num_nodes == 203_769
        assert fs.num_features == 166
        assert fs.edge_index.shape[1] == 234_355

    def test_published_label_counts(self):
        fs = load_elliptic(REAL_DIR)

        illicit = int(((fs.y == ILLICIT_CLASS) & fs.labelled_mask).sum())
        licit = int(((fs.y == LICIT_CLASS) & fs.labelled_mask).sum())
        assert illicit == 4_545
        assert licit == 42_019

    def test_paper_split_sizes_are_sane(self):
        fs = load_elliptic(REAL_DIR)
        train_mask, test_mask = time_step_split(fs, train_last_step=34)

        assert train_mask.sum() > test_mask.sum() > 0
