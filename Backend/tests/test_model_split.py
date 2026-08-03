"""
Tests for the model (ml/model.py), the split and scaler (ml/split.py), and the
training loop (ml/train.py).

The properties that matter most here are the ones that are silently violable:
the scaler must never see validation or test rows, the split must never overlap,
and SMOTE must run on embeddings rather than raw features.
"""

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torch_geometric")

from ml.evaluate import GroundTruth  # noqa: E402
from ml.features import FeatureSet  # noqa: E402
from ml.model import (  # noqa: E402
    GraphSAGERiskClassifier,
    pick_device,
    predict_scores,
)
from ml.split import (  # noqa: E402
    FeatureScaler,
    SplitMasks,
    binary_labels_from_mask,
    temporal_split,
)
from ml.train import (  # noqa: E402
    TrainConfig,
    _best_f1_threshold,
    load_feature_cache,
    render_result,
    save_feature_cache,
    train_model,
)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


class TestModel:
    def test_forward_returns_per_node_logits(self):
        model = GraphSAGERiskClassifier(in_channels=8, hidden=16, num_classes=2)
        x = torch.randn(10, 8)
        edge_index = torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long)

        out = model(x, edge_index)

        assert out.shape == (10, 2)
        assert torch.isfinite(out).all()

    def test_encode_and_classify_compose_to_forward(self):
        """The seam SMOTE depends on must actually be equivalent."""
        torch.manual_seed(0)
        model = GraphSAGERiskClassifier(in_channels=6, hidden=12, dropout=0.0)
        model.eval()
        x = torch.randn(8, 6)
        edge_index = torch.tensor([[0, 1], [1, 2]], dtype=torch.long)

        with torch.no_grad():
            direct = model(x, edge_index)
            staged = model.classify(model.encode(x, edge_index))

        assert torch.allclose(direct, staged, atol=1e-6)

    def test_encode_returns_hidden_width(self):
        model = GraphSAGERiskClassifier(in_channels=5, hidden=32)
        h = model.encode(torch.randn(7, 5), torch.tensor([[0], [1]], dtype=torch.long))
        assert h.shape == (7, 32)

    def test_layer_count_controls_hops(self):
        two = GraphSAGERiskClassifier(in_channels=4, hidden=8, num_layers=2)
        three = GraphSAGERiskClassifier(in_channels=4, hidden=8, num_layers=3)
        assert len(two.convs) == 2
        assert len(three.convs) == 3
        assert three.num_parameters() > two.num_parameters()

    def test_is_inductive_over_unseen_nodes(self):
        """Weight shapes depend on feature width, never node count."""
        model = GraphSAGERiskClassifier(in_channels=4, hidden=8)
        model.eval()
        small_edges = torch.tensor([[0], [1]], dtype=torch.long)
        large_edges = torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long)

        with torch.no_grad():
            small = model(torch.randn(3, 4), small_edges)
            large = model(torch.randn(9, 4), large_edges)

        assert small.shape == (3, 2)
        assert large.shape == (9, 2)

    def test_gradients_reach_the_first_conv(self):
        model = GraphSAGERiskClassifier(in_channels=4, hidden=8)
        out = model(torch.randn(6, 4), torch.tensor([[0, 1], [1, 2]], dtype=torch.long))
        out.sum().backward()

        first = model.convs[0].lin_l.weight
        assert first.grad is not None
        assert torch.isfinite(first.grad).all()

    def test_predict_scores_are_probabilities(self):
        model = GraphSAGERiskClassifier(in_channels=4, hidden=8)
        scores = predict_scores(
            model, torch.randn(12, 4), torch.tensor([[0], [1]], dtype=torch.long)
        )
        assert scores.shape == (12,)
        assert float(scores.min()) >= 0.0
        assert float(scores.max()) <= 1.0

    def test_rejects_bad_config(self):
        with pytest.raises(ValueError, match="num_layers"):
            GraphSAGERiskClassifier(in_channels=4, num_layers=0)
        with pytest.raises(ValueError, match="dropout"):
            GraphSAGERiskClassifier(in_channels=4, dropout=1.5)

    def test_pick_device_returns_a_device(self):
        assert isinstance(pick_device("cpu"), torch.device)
        assert pick_device("cpu").type == "cpu"
        assert isinstance(pick_device("auto"), torch.device)


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------


class TestTemporalSplit:
    def test_splits_are_disjoint_and_complete(self):
        times = np.arange(1_000, 1_100, dtype="float64")
        masks = temporal_split(times, train_frac=0.6, val_frac=0.2)

        assert not (masks.train & masks.val).any()
        assert not (masks.train & masks.test).any()
        assert not (masks.val & masks.test).any()
        assert (masks.train | masks.val | masks.test).all()

    def test_ordering_is_chronological(self):
        """Train must be strictly earlier than val, and val than test."""
        times = np.arange(1_000, 1_100, dtype="float64")
        masks = temporal_split(times, train_frac=0.6, val_frac=0.2)

        assert times[masks.train].max() <= times[masks.val].min()
        assert times[masks.val].max() <= times[masks.test].min()

    def test_unknown_timestamps_go_to_train(self):
        """Putting them in test would quietly depress the reported score."""
        times = np.array([100.0, 200.0, np.nan, 300.0, 0.0], dtype="float64")
        masks = temporal_split(times, train_frac=0.5, val_frac=0.25)

        assert masks.train[2]      # NaN
        assert masks.train[4]      # zero
        assert not masks.test[2]

    def test_fractions_control_sizes(self):
        times = np.arange(1_000, 2_000, dtype="float64")
        masks = temporal_split(times, train_frac=0.8, val_frac=0.1)

        assert int(masks.train.sum()) == pytest.approx(800, abs=5)
        assert int(masks.val.sum()) == pytest.approx(100, abs=5)

    def test_summary_reports_positives(self):
        times = np.arange(100, dtype="float64") + 1
        masks = temporal_split(times, train_frac=0.6, val_frac=0.2)
        positives = np.zeros(100, dtype=bool)
        positives[::10] = True

        text = masks.summary(positives)
        assert "train=" in text and "positives" in text

    def test_rejects_impossible_fractions(self):
        times = np.arange(10, dtype="float64") + 1
        with pytest.raises(ValueError):
            temporal_split(times, train_frac=0.9, val_frac=0.2)
        with pytest.raises(ValueError):
            temporal_split(times, train_frac=1.5)

    def test_no_usable_timestamps_raises(self):
        with pytest.raises(ValueError, match="no usable node timestamps"):
            temporal_split(np.array([np.nan, 0.0, -5.0]))


class TestRandomSplit:
    """Diagnostic split. Exists to separate 'model cannot learn' from
    'the chronological split creates distribution shift'."""

    def test_splits_are_disjoint_and_complete(self):
        from ml.split import random_split

        masks = random_split(1_000, train_frac=0.7, val_frac=0.15, seed=1)

        assert not (masks.train & masks.val).any()
        assert not (masks.train & masks.test).any()
        assert not (masks.val & masks.test).any()
        assert (masks.train | masks.val | masks.test).all()

    def test_is_reproducible(self):
        from ml.split import random_split

        first = random_split(500, seed=7)
        second = random_split(500, seed=7)
        assert np.array_equal(first.train, second.train)

    def test_ignores_chronology(self):
        """Unlike temporal_split, later nodes must land in train too."""
        from ml.split import random_split

        masks = random_split(1_000, seed=3)
        # The last decile of node indices should not be confined to test.
        assert masks.train[900:].any()

    def test_rejects_impossible_fractions(self):
        from ml.split import random_split

        with pytest.raises(ValueError):
            random_split(100, train_frac=0.9, val_frac=0.2)


class TestFeatureScaler:
    def test_fits_on_train_rows_only(self):
        """The leakage guard. Test rows must not move the fitted statistics."""
        x = np.zeros((100, 2), dtype=np.float32)
        x[:50, 0] = 1.0
        x[50:, 0] = 1_000_000.0        # a wildly different test distribution
        train_mask = np.zeros(100, dtype=bool)
        train_mask[:50] = True

        fitted_on_train = FeatureScaler().fit(x, train_mask)
        train_only = np.zeros((50, 2), dtype=np.float32)
        train_only[:, 0] = 1.0
        fitted_on_subset = FeatureScaler().fit(train_only, np.ones(50, dtype=bool))

        assert fitted_on_train.mean_[0] == pytest.approx(fitted_on_subset.mean_[0])

    def test_collapses_the_magnitude_spread(self):
        """The real matrix spans ~1e18 between its largest and smallest column."""
        x = np.zeros((200, 2), dtype=np.float32)
        rng = np.random.default_rng(0)
        x[:, 0] = rng.uniform(0, 1.5e14, size=200)      # amounts
        x[:, 1] = rng.uniform(0, 1.4e-4, size=200)      # pagerank
        train_mask = np.ones(200, dtype=bool)

        scaled = FeatureScaler().fit_transform(x, train_mask)

        assert np.isfinite(scaled).all()
        assert float(np.abs(scaled).max()) < 20.0

    def test_signed_log_survives_negative_values(self):
        """net_flow is legitimately negative; plain log1p would give NaN."""
        x = np.array([[-1e9], [0.0], [1e9]], dtype=np.float32)
        scaled = FeatureScaler().fit_transform(x, np.ones(3, dtype=bool))

        assert np.isfinite(scaled).all()
        assert scaled[0, 0] < scaled[1, 0] < scaled[2, 0]

    def test_monotonic_within_a_column(self):
        x = np.array([[1.0], [10.0], [100.0], [1000.0]], dtype=np.float32)
        scaled = FeatureScaler().fit_transform(x, np.ones(4, dtype=bool))
        assert np.all(np.diff(scaled[:, 0]) > 0)

    def test_constant_column_becomes_zero_not_nan(self):
        x = np.ones((10, 2), dtype=np.float32)
        x[:, 1] = np.arange(10)
        scaled = FeatureScaler().fit_transform(x, np.ones(10, dtype=bool))

        assert np.isfinite(scaled).all()
        assert np.allclose(scaled[:, 0], 0.0)

    def test_transform_before_fit_raises(self):
        with pytest.raises(RuntimeError, match="fitted"):
            FeatureScaler().transform(np.ones((3, 2), dtype=np.float32))

    def test_empty_train_split_raises(self):
        with pytest.raises(ValueError, match="empty train split"):
            FeatureScaler().fit(np.ones((5, 2), dtype=np.float32), np.zeros(5, dtype=bool))

    def test_binary_labels_helper(self):
        labels = binary_labels_from_mask(np.array([True, False, True]))
        assert labels.dtype == np.int64
        assert labels.tolist() == [1, 0, 1]


# ---------------------------------------------------------------------------
# training
# ---------------------------------------------------------------------------


def _synthetic_feature_set(
    num_nodes: int = 400, num_features: int = 10, positives: int = 40
):
    """A graph where the positive class is genuinely learnable.

    Positives sit in a distinct feature region AND link to each other, so both
    the features and the message passing carry signal — otherwise a training
    test cannot distinguish "the loop works" from "the task is impossible".
    """
    rng = np.random.default_rng(0)
    x = rng.normal(0.0, 1.0, size=(num_nodes, num_features)).astype(np.float32)
    positive_idx = rng.choice(num_nodes, size=positives, replace=False)
    x[positive_idx] += 4.0

    sources: list = []
    targets: list = []
    for i in range(num_nodes - 1):
        sources.append(i)
        targets.append(i + 1)
    # Wire the positives together so neighbourhoods differ too.
    for a, b in zip(positive_idx[:-1], positive_idx[1:]):
        sources.append(int(a))
        targets.append(int(b))

    edge_index = np.vstack([sources, targets]).astype(np.int64)
    truth = np.zeros(num_nodes, dtype=bool)
    truth[positive_idx] = True

    node_ids = [f"acct{i}" for i in range(num_nodes)]
    return (
        FeatureSet(
            node_ids=node_ids,
            x=x,
            y=truth.astype(np.int64) * 3,
            labelled_mask=np.zeros(num_nodes, dtype=bool),
            edge_index=edge_index,
            edge_weight=np.ones(edge_index.shape[1], dtype=np.float32),
            feature_names=[f"f{i}" for i in range(num_features)],
            # Spread across time so a chronological split is meaningful, with
            # positives present throughout.
            node_first_ts=np.linspace(1_600_000_000, 1_600_500_000, num_nodes),
        ),
        GroundTruth(
            accounts_by_typology={"CYCLE": {node_ids[i] for i in positive_idx}}
        ),
    )


class TestTrainingLoop:
    def test_trains_end_to_end_and_learns(self):
        feature_set, ground_truth = _synthetic_feature_set()
        config = TrainConfig(epochs=40, hidden=16, patience=40, device="cpu")

        model, result = train_model(feature_set, ground_truth, config)

        assert result.best_epoch >= 1
        # A separable task must beat random ranking by a wide margin.
        assert result.best_val_pr_auc > 0.5
        assert result.test_metrics["average_precision"] > 0.5
        assert 0.0 <= result.threshold <= 1.0
        assert len(result.history) >= 1

    def test_scaler_is_fitted_before_tensors_are_built(self):
        """Training must not blow up on unscaled heavy-tailed inputs."""
        feature_set, ground_truth = _synthetic_feature_set()
        feature_set.x[:, 0] *= 1e12

        _, result = train_model(
            feature_set, ground_truth, TrainConfig(epochs=5, hidden=8, device="cpu")
        )
        assert np.isfinite(result.history[-1]["loss"])

    def test_smote_path_runs(self):
        feature_set, ground_truth = _synthetic_feature_set()
        config = TrainConfig(
            epochs=5, hidden=8, device="cpu", use_smote=True, smote_ratio=1.0
        )

        _, result = train_model(feature_set, ground_truth, config)
        assert np.isfinite(result.history[-1]["loss"])

    def test_reports_per_typology_recall(self):
        feature_set, ground_truth = _synthetic_feature_set()
        _, result = train_model(
            feature_set, ground_truth, TrainConfig(epochs=10, hidden=8, device="cpu")
        )
        assert "CYCLE" in result.typology_recall

    def test_missing_timestamps_rejected(self):
        feature_set, ground_truth = _synthetic_feature_set()
        feature_set.node_first_ts = None

        with pytest.raises(ValueError, match="node_first_ts"):
            train_model(feature_set, ground_truth, TrainConfig(epochs=1, device="cpu"))

    def test_split_without_positives_is_rejected(self):
        """Silent zero-positive splits would produce meaningless metrics."""
        feature_set, ground_truth = _synthetic_feature_set()
        node_ids = feature_set.node_ids
        # Confine every positive to the earliest nodes, so test gets none.
        ground_truth.accounts_by_typology = {"CYCLE": set(node_ids[:20])}

        with pytest.raises(ValueError, match="no positives"):
            train_model(
                feature_set, ground_truth, TrainConfig(epochs=1, device="cpu")
            )

    def test_render_result_mentions_the_key_numbers(self):
        feature_set, ground_truth = _synthetic_feature_set()
        _, result = train_model(
            feature_set, ground_truth, TrainConfig(epochs=5, hidden=8, device="cpu")
        )
        text = render_result(result)

        assert "PR-AUC" in text
        assert "RECALL BY TYPOLOGY" in text
        assert "VS THE DETECTORS" in text
        assert "meaningless here" in text        # the accuracy caveat


class TestThresholdSelection:
    def test_picks_a_separating_threshold(self):
        truth = np.array([False] * 90 + [True] * 10)
        scores = np.concatenate([np.linspace(0.0, 0.4, 90), np.linspace(0.8, 1.0, 10)])

        threshold = _best_f1_threshold(truth, scores)

        assert 0.4 < threshold <= 0.8

    def test_degenerate_input_returns_default(self):
        assert _best_f1_threshold(np.zeros(5, dtype=bool), np.linspace(0, 1, 5)) == 0.5
        assert _best_f1_threshold(np.ones(5, dtype=bool), np.linspace(0, 1, 5)) == 0.5


class TestFeatureCache:
    def test_round_trips(self, tmp_path):
        feature_set, _ = _synthetic_feature_set(num_nodes=50, num_features=4)
        path = Path(tmp_path) / "cache.npz"

        save_feature_cache(feature_set, path)
        restored = load_feature_cache(path)

        assert restored.node_ids == feature_set.node_ids
        assert np.allclose(restored.x, feature_set.x)
        assert np.array_equal(restored.edge_index, feature_set.edge_index)
        assert restored.feature_names == feature_set.feature_names
        assert np.allclose(restored.node_first_ts, feature_set.node_first_ts)
