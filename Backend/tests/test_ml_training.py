"""
Tests for Focal Loss, embedding-level SMOTE, and the evaluation layer.

The SMOTE tests are the ones that encode the design decision: interpolation
must happen on post-convolution embeddings and must stay differentiable, or
the graph encoder learns nothing from the synthetic samples.
"""

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from ml.evaluate import (  # noqa: E402
    ALL_TYPOLOGIES,
    NON_CYCLE_TYPOLOGIES,
    GroundTruth,
    compare_against_detector,
    fraud_metrics,
    load_ground_truth,
    recall_by_typology,
    risk_scores_from_logits,
)
from ml.imbalance import smote_embeddings  # noqa: E402
from ml.losses import FocalLoss, class_balanced_alpha  # noqa: E402


PATTERNS_PATH = Path("benchmarks/data/HI-Small_Patterns.txt")


# ---------------------------------------------------------------------------
# Focal Loss
# ---------------------------------------------------------------------------


class TestFocalLoss:
    def test_down_weights_easy_examples(self):
        """The core property: a confident correct prediction contributes little.

        gamma=2 scales an example at p_t=0.9 by (1-0.9)^2 = 0.01.
        """
        easy = torch.tensor([[5.0, -5.0]])     # very confident, correct
        hard = torch.tensor([[0.1, -0.1]])     # barely correct
        target = torch.tensor([0])

        focal = FocalLoss(gamma=2.0, alpha=None, reduction="none")
        easy_loss = focal(easy, target).item()
        hard_loss = focal(hard, target).item()

        assert easy_loss < hard_loss
        assert easy_loss < 0.01 * hard_loss

    def test_gamma_zero_matches_cross_entropy(self):
        """With gamma=0 and no alpha, Focal Loss reduces to cross-entropy."""
        import torch.nn.functional as F

        logits = torch.tensor([[2.0, 0.5, -1.0, 0.2], [0.1, 1.5, 0.3, -0.4]])
        target = torch.tensor([0, 1])

        focal = FocalLoss(gamma=0.0, alpha=None, reduction="mean")
        assert focal(logits, target).item() == pytest.approx(
            F.cross_entropy(logits, target).item(), abs=1e-6
        )

    def test_higher_gamma_focuses_harder(self):
        logits = torch.tensor([[3.0, -3.0]])
        target = torch.tensor([0])

        loss_g0 = FocalLoss(gamma=0.0, alpha=None, reduction="none")(logits, target)
        loss_g2 = FocalLoss(gamma=2.0, alpha=None, reduction="none")(logits, target)
        loss_g5 = FocalLoss(gamma=5.0, alpha=None, reduction="none")(logits, target)

        # This example is easy, so more focusing means less loss.
        assert loss_g5.item() < loss_g2.item() < loss_g0.item()

    def test_ignore_index_excludes_unlabelled_nodes(self):
        """Unlabelled accounts must not train the model toward 'low risk'."""
        logits = torch.tensor([[2.0, 0.0], [0.0, 2.0], [1.0, 1.0]])
        # Third node unlabelled.
        target = torch.tensor([0, 1, -100])

        focal = FocalLoss(gamma=2.0, alpha=None, reduction="mean")
        masked = focal(logits, target)
        only_labelled = focal(logits[:2], target[:2])

        assert masked.item() == pytest.approx(only_labelled.item(), abs=1e-6)

    def test_all_unlabelled_returns_zero_with_grad(self):
        """An all-unlabelled batch is a no-op, not a crash."""
        logits = torch.randn(3, 4, requires_grad=True)
        target = torch.full((3,), -100)

        loss = FocalLoss()(logits, target)

        assert loss.item() == pytest.approx(0.0)
        loss.backward()  # must not raise
        assert logits.grad is not None

    def test_per_class_alpha_reweights(self):
        """A per-class alpha actually rebalances; a scalar only rescales."""
        logits = torch.tensor([[0.5, 0.2, 0.1, 0.0]])
        target_low = torch.tensor([0])
        target_critical = torch.tensor([3])

        # Heavily favour the critical class.
        alpha = [0.1, 1.0, 1.0, 5.0]
        focal = FocalLoss(gamma=2.0, alpha=alpha, reduction="none")

        low_loss = focal(logits, target_low).item()
        critical_loss = focal(logits, target_critical).item()
        assert critical_loss > low_loss

    def test_scalar_alpha_only_scales(self):
        logits = torch.tensor([[0.5, 0.2, 0.1, 0.0]])
        target = torch.tensor([2])

        unweighted = FocalLoss(gamma=2.0, alpha=None, reduction="none")(logits, target)
        scaled = FocalLoss(gamma=2.0, alpha=0.25, reduction="none")(logits, target)

        assert scaled.item() == pytest.approx(0.25 * unweighted.item(), abs=1e-6)

    def test_gradients_flow(self):
        logits = torch.randn(8, 4, requires_grad=True)
        target = torch.randint(0, 4, (8,))

        FocalLoss()(logits, target).backward()

        assert logits.grad is not None
        assert torch.isfinite(logits.grad).all()

    def test_rejects_out_of_range_target(self):
        logits = torch.randn(2, 4)
        with pytest.raises(ValueError, match="target values"):
            FocalLoss()(logits, torch.tensor([0, 9]))

    def test_rejects_bad_shapes(self):
        with pytest.raises(ValueError):
            FocalLoss()(torch.randn(4), torch.tensor([0]))
        with pytest.raises(ValueError):
            FocalLoss()(torch.randn(2, 4), torch.tensor([0, 1, 2]))

    def test_rejects_negative_gamma(self):
        with pytest.raises(ValueError, match="gamma"):
            FocalLoss(gamma=-1.0)


class TestClassBalancedAlpha:
    def test_rare_class_gets_more_weight(self):
        # 100 low, 5 critical.
        labels = torch.cat([torch.zeros(100), torch.full((5,), 3)]).long()
        weights = class_balanced_alpha(labels, num_classes=4)

        assert weights[3] > weights[0]

    def test_absent_classes_get_unit_weight_not_infinity(self):
        labels = torch.zeros(10).long()
        weights = class_balanced_alpha(labels, num_classes=4)

        assert torch.isfinite(weights).all()
        assert weights[1].item() == pytest.approx(1.0)

    def test_effective_number_is_gentler_than_inverse_frequency(self):
        labels = torch.cat([torch.zeros(1000), torch.full((3,), 3)]).long()

        inverse = class_balanced_alpha(labels, 4, beta=None)
        effective = class_balanced_alpha(labels, 4, beta=0.999)

        # Both up-weight the rare class, but inverse frequency far more.
        assert inverse[3] > effective[3]

    def test_ignore_index_excluded_from_counts(self):
        labels = torch.tensor([0, 0, 3, -100, -100])
        weights = class_balanced_alpha(labels, num_classes=4)
        assert torch.isfinite(weights).all()

    def test_rejects_bad_beta(self):
        with pytest.raises(ValueError, match="beta"):
            class_balanced_alpha(torch.zeros(4).long(), 4, beta=1.5)


# ---------------------------------------------------------------------------
# SMOTE on embeddings
# ---------------------------------------------------------------------------


class TestSmoteEmbeddings:
    def test_balances_the_minority_class(self):
        torch.manual_seed(0)
        embeddings = torch.cat([torch.randn(50, 8), torch.randn(5, 8) + 3.0])
        labels = torch.cat([torch.zeros(50), torch.ones(5)]).long()

        aug_x, aug_y = smote_embeddings(embeddings, labels)

        assert int((aug_y == 0).sum()) == 50
        assert int((aug_y == 1).sum()) == 50
        assert aug_x.shape[0] == aug_y.shape[0] == 100
        assert aug_x.shape[1] == 8

    def test_originals_are_preserved_and_come_first(self):
        torch.manual_seed(0)
        embeddings = torch.cat([torch.randn(20, 4), torch.randn(4, 4) + 5.0])
        labels = torch.cat([torch.zeros(20), torch.ones(4)]).long()

        aug_x, aug_y = smote_embeddings(embeddings, labels)

        assert torch.equal(aug_x[: len(embeddings)], embeddings)
        assert torch.equal(aug_y[: len(labels)], labels)

    def test_stays_differentiable(self):
        """The reason this is hand-written rather than an imblearn call.

        imblearn round-trips through numpy, which severs autograd and stops the
        conv layers learning from synthetic samples.
        """
        torch.manual_seed(0)
        base = torch.randn(30, 6, requires_grad=True)
        embeddings = base * 2.0            # a differentiable op, as a conv would be
        labels = torch.cat([torch.zeros(25), torch.ones(5)]).long()

        aug_x, _ = smote_embeddings(embeddings, labels)

        assert aug_x.requires_grad, "synthetic samples must keep the autograd path"
        aug_x.sum().backward()
        assert base.grad is not None
        assert torch.isfinite(base.grad).all()

    def test_synthetic_samples_lie_between_real_ones(self):
        """Interpolation, not extrapolation: new points stay inside the hull."""
        # Minority points all in [10, 11] on every axis.
        majority = torch.zeros(20, 3)
        minority = torch.rand(5, 3) + 10.0
        embeddings = torch.cat([majority, minority])
        labels = torch.cat([torch.zeros(20), torch.ones(5)]).long()

        aug_x, aug_y = smote_embeddings(embeddings, labels)
        synthetic = aug_x[len(embeddings):]

        assert synthetic.shape[0] > 0
        assert bool((synthetic >= minority.min()).all())
        assert bool((synthetic <= minority.max()).all())

    def test_single_member_class_is_skipped_not_duplicated(self):
        """Interpolation needs two points; duplicating one adds no information."""
        embeddings = torch.cat([torch.randn(20, 4), torch.randn(1, 4)])
        labels = torch.cat([torch.zeros(20), torch.ones(1)]).long()

        aug_x, aug_y = smote_embeddings(embeddings, labels)

        assert aug_x.shape[0] == 21          # unchanged
        assert int((aug_y == 1).sum()) == 1

    def test_already_balanced_input_is_untouched(self):
        embeddings = torch.randn(20, 4)
        labels = torch.cat([torch.zeros(10), torch.ones(10)]).long()

        aug_x, aug_y = smote_embeddings(embeddings, labels)

        assert torch.equal(aug_x, embeddings)
        assert torch.equal(aug_y, labels)

    def test_target_ratio_controls_amount(self):
        torch.manual_seed(0)
        embeddings = torch.cat([torch.randn(100, 4), torch.randn(10, 4) + 3.0])
        labels = torch.cat([torch.zeros(100), torch.ones(10)]).long()

        _, half = smote_embeddings(embeddings, labels, target_ratio=0.5)
        _, full = smote_embeddings(embeddings, labels, target_ratio=1.0)

        assert int((half == 1).sum()) == 50
        assert int((full == 1).sum()) == 100

    def test_k_neighbours_clamped_to_class_size(self):
        """A 3-member class must still work with k_neighbours=5."""
        torch.manual_seed(0)
        embeddings = torch.cat([torch.randn(20, 4), torch.randn(3, 4) + 3.0])
        labels = torch.cat([torch.zeros(20), torch.ones(3)]).long()

        aug_x, aug_y = smote_embeddings(embeddings, labels, k_neighbours=5)
        assert int((aug_y == 1).sum()) == 20

    def test_handles_four_class_imbalance(self):
        """The real shape: low/medium/high/critical, fraud rare."""
        torch.manual_seed(0)
        embeddings = torch.cat([
            torch.randn(200, 8),
            torch.randn(20, 8) + 1.0,
            torch.randn(8, 8) + 2.0,
            torch.randn(4, 8) + 3.0,
        ])
        labels = torch.cat([
            torch.zeros(200), torch.ones(20),
            torch.full((8,), 2), torch.full((4,), 3),
        ]).long()

        _, aug_y = smote_embeddings(embeddings, labels)

        counts = [int((aug_y == c).sum()) for c in range(4)]
        assert counts == [200, 200, 200, 200]

    def test_reproducible_with_generator(self):
        embeddings = torch.cat([torch.randn(30, 4), torch.randn(5, 4) + 3.0])
        labels = torch.cat([torch.zeros(30), torch.ones(5)]).long()

        g1 = torch.Generator().manual_seed(42)
        g2 = torch.Generator().manual_seed(42)
        first, _ = smote_embeddings(embeddings, labels, generator=g1)
        second, _ = smote_embeddings(embeddings, labels, generator=g2)

        assert torch.allclose(first, second)

    def test_empty_input_returns_empty(self):
        aug_x, aug_y = smote_embeddings(torch.zeros((0, 4)), torch.zeros((0,)).long())
        assert aug_x.shape[0] == 0

    def test_rejects_bad_arguments(self):
        embeddings = torch.randn(10, 4)
        labels = torch.zeros(10).long()
        with pytest.raises(ValueError):
            smote_embeddings(embeddings, labels, target_ratio=0.0)
        with pytest.raises(ValueError):
            smote_embeddings(embeddings, labels, k_neighbours=0)
        with pytest.raises(ValueError):
            smote_embeddings(torch.randn(10), labels)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestFraudMetrics:
    def test_all_negative_predictor_is_exposed(self):
        """The point of not reporting accuracy alone.

        99 clean, 1 fraud, predict all clean: 99% accuracy, 0% recall.
        """
        y_true = np.zeros(100, dtype=bool)
        y_true[0] = True
        y_pred = np.zeros(100, dtype=bool)

        m = fraud_metrics(y_true, y_pred)

        assert m.accuracy == pytest.approx(0.99)
        assert m.recall == 0.0
        assert m.precision == 0.0
        assert m.f1 == 0.0
        assert m.prevalence == pytest.approx(0.01)

    def test_perfect_prediction(self):
        y_true = np.array([True, True, False, False])
        m = fraud_metrics(y_true, y_true)

        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.false_positives == 0

    def test_counts_are_correct(self):
        y_true = np.array([True, True, True, False, False])
        y_pred = np.array([True, False, True, True, False])

        m = fraud_metrics(y_true, y_pred)

        assert m.true_positives == 2
        assert m.false_negatives == 1
        assert m.false_positives == 1
        assert m.support == 3
        assert m.recall == pytest.approx(2 / 3)
        assert m.precision == pytest.approx(2 / 3)

    def test_pr_auc_uses_scores(self):
        y_true = np.array([True, True, False, False])
        y_pred = np.array([True, False, False, False])
        # Scores rank both positives above both negatives.
        y_score = np.array([0.9, 0.8, 0.2, 0.1])

        m = fraud_metrics(y_true, y_pred, y_score)

        assert m.average_precision == pytest.approx(1.0)
        assert m.roc_auc == pytest.approx(1.0)
        # Ranking is perfect even though the hard threshold missed one.
        assert m.recall == pytest.approx(0.5)

    def test_single_class_skips_threshold_free_metrics(self):
        y_true = np.zeros(5, dtype=bool)
        m = fraud_metrics(y_true, np.zeros(5, dtype=bool), np.linspace(0, 1, 5))
        assert m.roc_auc is None

    def test_rejects_empty_and_mismatched(self):
        with pytest.raises(ValueError):
            fraud_metrics(np.array([]), np.array([]))
        with pytest.raises(ValueError):
            fraud_metrics(np.array([True]), np.array([True, False]))

    def test_summary_flags_accuracy_as_untrustworthy(self):
        m = fraud_metrics(np.array([True, False]), np.array([True, False]))
        assert "ignore this one" in m.summary()


class TestRiskScoresFromLogits:
    def test_score_is_one_minus_p_low(self):
        # Class 0 dominant -> low risk; class 3 dominant -> high risk.
        logits = torch.tensor([[10.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 10.0]])
        scores = risk_scores_from_logits(logits)

        assert scores[0] < 0.01
        assert scores[1] > 0.99

    def test_scores_are_in_unit_range(self):
        scores = risk_scores_from_logits(torch.randn(20, 4))
        assert scores.min() >= 0.0
        assert scores.max() <= 1.0

    def test_rejects_wrong_rank(self):
        with pytest.raises(ValueError):
            risk_scores_from_logits(torch.randn(5))


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------


class TestGroundTruthUnit:
    def test_labels_align_positionally_with_node_ids(self):
        gt = GroundTruth(accounts_by_typology={"CYCLE": {"a", "c"}})
        labels = gt.labels_for(["a", "b", "c", "d"])

        assert labels.tolist() == [True, False, True, False]
        assert labels.dtype == np.bool_

    def test_all_accounts_unions_typologies(self):
        gt = GroundTruth(
            accounts_by_typology={"CYCLE": {"a"}, "FAN-OUT": {"b"}, "STACK": set()}
        )
        assert gt.all_accounts == {"a", "b"}

    def test_typology_labels_are_scoped(self):
        gt = GroundTruth(accounts_by_typology={"CYCLE": {"a"}, "FAN-OUT": {"b"}})

        assert gt.typology_labels_for(["a", "b"], "CYCLE").tolist() == [True, False]
        assert gt.typology_labels_for(["a", "b"], "FAN-OUT").tolist() == [False, True]

    def test_recall_by_typology_skips_unsupported(self):
        gt = GroundTruth(accounts_by_typology={"CYCLE": {"a", "b"}, "STACK": set()})
        node_ids = ["a", "b", "c"]
        y_pred = np.array([True, False, True])

        result = recall_by_typology(node_ids, y_pred, gt, ["CYCLE", "STACK"])

        assert result["CYCLE"]["recall"] == pytest.approx(0.5)
        assert "STACK" not in result       # zero support, not reported as 0.0

    def test_compare_against_detector_finds_gnn_only_wins(self):
        """The headline number: correct flags the detectors missed."""
        gt = GroundTruth(accounts_by_typology={"FAN-OUT": {"a", "b"}})
        node_ids = ["a", "b", "c"]
        # GNN flags a and c; the detector flagged nothing.
        gnn_pred = np.array([True, False, True])
        detector = np.array([False, False, False])

        result = compare_against_detector(node_ids, gnn_pred, detector, gt)

        assert result["detector_recall"] == 0.0
        assert result["gnn_recall"] == pytest.approx(0.5)
        assert result["gnn_only_flagged"] == 2       # a and c
        assert result["gnn_only_correct"] == 1       # only a is real in ground truth
        assert result["missed_by_both"] == 1         # b
        assert result["gnn_only_correct_accounts"] == ["a"]


@pytest.mark.skipif(
    not PATTERNS_PATH.exists(),
    reason="IBM AML patterns file not present in benchmarks/data/",
)
class TestGroundTruthAgainstRealFile:
    """Runs against the real 475MB-dataset companion patterns file."""

    def test_loads_all_eight_typologies(self):
        gt = load_ground_truth(PATTERNS_PATH)

        populated = {t for t, v in gt.accounts_by_typology.items() if v}
        assert populated == set(ALL_TYPOLOGIES)

    def test_non_cycle_typologies_dominate(self):
        """Why this file is worth using: CYCLE is the minority of the truth."""
        gt = load_ground_truth(PATTERNS_PATH)

        cycle = len(gt.accounts_by_typology["CYCLE"])
        non_cycle = len(
            set().union(*(gt.accounts_by_typology[t] for t in NON_CYCLE_TYPOLOGIES))
        )
        assert non_cycle > cycle

    def test_account_ids_are_32_char_hashes(self):
        """Must match the _account_key hashing the ingestor writes to Neo4j."""
        gt = load_ground_truth(PATTERNS_PATH)
        sample = sorted(gt.all_accounts)[:5]

        assert len(sample) == 5
        for account_id in sample:
            assert len(account_id) == 32
            int(account_id, 16)        # valid hex

    def test_ground_truth_has_thousands_of_accounts(self):
        gt = load_ground_truth(PATTERNS_PATH)
        assert len(gt.all_accounts) > 1_000
