"""
Honest measurement for the GNN risk classifier.

THE PROBLEM THIS SOLVES
-----------------------
Training labels come from risk_flags, which the cycle detector produces. Scoring
the model against those same labels only measures how well it imitates a
heuristic — and it is blind in exactly the places the heuristic is blind. A GNN
that agrees with the cycle detector 100% is an expensive reimplementation of it.

The IBM AML dataset ships real ground truth: HI-Small_Patterns.txt labels 370
laundering attempts across 8 typologies, resolving to ~3,170 accounts. Only 54
of those attempts are CYCLE. FAN-OUT, FAN-IN, BIPARTITE, STACK, GATHER-SCATTER
and SCATTER-GATHER are structurally invisible to cycle detection, so they are
the real test of whether the GNN learned something its teacher never knew.

Account IDs line up for free: patterns.py and ingestor.py both key accounts
through the same _account_key sha256 hash, so ground-truth IDs are the same
strings as the Neo4j node ids in FeatureSet.node_ids.

WHY NOT ACCURACY
----------------
At ~1% prevalence, "everything is low risk" scores 99% accuracy and catches
nothing. Every metric here is computed on the positive (laundering) class, and
accuracy is reported only alongside prevalence so it cannot be read alone.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Set, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)


# Every typology in HI-Small_Patterns.txt. CYCLE first because it is the only
# one the current detectors can find on their own.
ALL_TYPOLOGIES: Tuple[str, ...] = (
    "CYCLE",
    "FAN-OUT",
    "FAN-IN",
    "GATHER-SCATTER",
    "SCATTER-GATHER",
    "BIPARTITE",
    "STACK",
    "RANDOM",
)

# Typologies with no closed loop, so depth-limited cycle DFS cannot see them
# however deep it searches. Recall here is the interesting number.
NON_CYCLE_TYPOLOGIES: Tuple[str, ...] = tuple(
    t for t in ALL_TYPOLOGIES if t != "CYCLE"
)

DEFAULT_PATTERNS_PATH = "benchmarks/data/HI-Small_Patterns.txt"


@dataclass
class GroundTruth:
    """Real laundering labels, keyed by the same hashed account id as Neo4j."""

    accounts_by_typology: Dict[str, Set[str]] = field(default_factory=dict)

    @property
    def all_accounts(self) -> Set[str]:
        result: Set[str] = set()
        for accounts in self.accounts_by_typology.values():
            result |= accounts
        return result

    def labels_for(self, node_ids: Sequence[str]) -> np.ndarray:
        """Boolean [num_nodes] array: is this account in a laundering pattern?

        Aligned positionally with node_ids, so it lines up with FeatureSet.x
        and FeatureSet.y row for row.
        """
        laundering = self.all_accounts
        return np.fromiter(
            (node_id in laundering for node_id in node_ids),
            dtype=bool,
            count=len(node_ids),
        )

    def typology_labels_for(
        self, node_ids: Sequence[str], typology: str
    ) -> np.ndarray:
        """Boolean array for one typology only."""
        accounts = self.accounts_by_typology.get(typology.upper(), set())
        return np.fromiter(
            (node_id in accounts for node_id in node_ids),
            dtype=bool,
            count=len(node_ids),
        )


def load_ground_truth(
    patterns_path: Union[str, Path] = DEFAULT_PATTERNS_PATH,
    typologies: Iterable[str] = ALL_TYPOLOGIES,
) -> GroundTruth:
    """Parse HI-Small_Patterns.txt into per-typology account sets.

    Reuses benchmarks.ibm_aml.patterns rather than re-parsing, so the account
    hashing stays identical to what the ingestor wrote into Neo4j.
    """
    from benchmarks.ibm_aml.patterns import load_pattern_groups

    wanted = [t.upper() for t in typologies]
    groups = load_pattern_groups(patterns_path, wanted)

    accounts_by_typology: Dict[str, Set[str]] = {t: set() for t in wanted}
    for group in groups:
        accounts_by_typology.setdefault(group.typology.upper(), set()).update(
            group.account_set
        )

    total = sum(len(v) for v in accounts_by_typology.values())
    logger.info(
        "Ground truth loaded: %d groups, %d typologies, %d account-typology pairs",
        len(groups),
        len([t for t, v in accounts_by_typology.items() if v]),
        total,
    )
    return GroundTruth(accounts_by_typology=accounts_by_typology)


@dataclass
class FraudMetrics:
    """Scores on the positive (laundering) class."""

    precision: float
    recall: float
    f1: float
    average_precision: float          # PR-AUC; the headline number at low prevalence
    roc_auc: Optional[float]
    support: int                      # true positives available
    predicted_positive: int
    true_positives: int
    false_positives: int
    false_negatives: int
    prevalence: float
    accuracy: float                   # reported ONLY next to prevalence

    def summary(self) -> str:
        return (
            f"precision={self.precision:.3f} recall={self.recall:.3f} "
            f"f1={self.f1:.3f} pr_auc={self.average_precision:.3f} "
            f"(support={self.support}, prevalence={self.prevalence:.2%}, "
            f"accuracy={self.accuracy:.3f} <- ignore this one)"
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "average_precision": self.average_precision,
            "roc_auc": self.roc_auc,
            "support": self.support,
            "predicted_positive": self.predicted_positive,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "prevalence": self.prevalence,
            "accuracy": self.accuracy,
        }


def fraud_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_score: Optional[np.ndarray] = None,
) -> FraudMetrics:
    """Positive-class metrics for a binary laundering decision.

    Args:
        y_true:  [N] bool/0-1 ground truth.
        y_pred:  [N] bool/0-1 hard predictions.
        y_score: [N] float risk scores, for the threshold-free metrics.
                 Strongly recommended: precision and recall depend entirely on
                 where you set the threshold, whereas PR-AUC summarizes every
                 threshold at once.
    """
    from sklearn.metrics import average_precision_score, roc_auc_score

    y_true = np.asarray(y_true).astype(bool).ravel()
    y_pred = np.asarray(y_pred).astype(bool).ravel()
    if y_true.shape != y_pred.shape:
        raise ValueError(
            f"y_true {y_true.shape} and y_pred {y_pred.shape} must match"
        )
    if y_true.size == 0:
        raise ValueError("cannot compute metrics on an empty array")

    true_positives = int(np.sum(y_true & y_pred))
    false_positives = int(np.sum(~y_true & y_pred))
    false_negatives = int(np.sum(y_true & ~y_pred))
    support = int(np.sum(y_true))
    predicted_positive = int(np.sum(y_pred))

    precision = (
        true_positives / predicted_positive if predicted_positive else 0.0
    )
    recall = true_positives / support if support else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    average_precision = 0.0
    auc: Optional[float] = None
    # Both threshold-free metrics need at least one of each class present.
    if y_score is not None and 0 < support < y_true.size:
        scores = np.asarray(y_score, dtype=float).ravel()
        if scores.shape != y_true.shape:
            raise ValueError("y_score must have the same shape as y_true")
        average_precision = float(average_precision_score(y_true, scores))
        auc = float(roc_auc_score(y_true, scores))
    elif y_score is None:
        # Degenerate but honest: with hard labels only, AP collapses to precision.
        average_precision = precision

    return FraudMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        average_precision=average_precision,
        roc_auc=auc,
        support=support,
        predicted_positive=predicted_positive,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        prevalence=float(support / y_true.size),
        accuracy=float(np.mean(y_true == y_pred)),
    )


def risk_scores_from_logits(logits: Any) -> np.ndarray:
    """Collapse [N, 4] class logits into one "how risky" score per account.

    Defined as 1 - P(low), i.e. the total probability mass on medium/high/
    critical. That ranks accounts sensibly for PR-AUC without committing to a
    threshold, and it does not throw away the distinction between a confident
    'medium' and a confident 'critical'.
    """
    import torch

    if not isinstance(logits, torch.Tensor):
        logits = torch.as_tensor(logits)
    if logits.ndim != 2:
        raise ValueError(f"expected [N, C] logits, got {tuple(logits.shape)}")

    with torch.no_grad():
        probabilities = torch.softmax(logits.float(), dim=-1)
        risk = 1.0 - probabilities[:, 0]
    return risk.cpu().numpy()


def recall_by_typology(
    node_ids: Sequence[str],
    y_pred: np.ndarray,
    ground_truth: GroundTruth,
    typologies: Iterable[str] = ALL_TYPOLOGIES,
) -> Dict[str, Dict[str, float]]:
    """Per-typology recall — the diagnostic that matters most here.

    Read it like this: strong CYCLE recall only shows the GNN learned what its
    labels taught it. Recall on FAN-OUT, BIPARTITE or STACK is evidence it
    generalized to structures the cycle detector cannot represent at all.
    """
    y_pred = np.asarray(y_pred).astype(bool).ravel()
    if y_pred.shape[0] != len(node_ids):
        raise ValueError("y_pred length must match node_ids length")

    results: Dict[str, Dict[str, float]] = {}
    for typology in typologies:
        key = typology.upper()
        truth = ground_truth.typology_labels_for(node_ids, key)
        support = int(truth.sum())
        if support == 0:
            continue
        caught = int(np.sum(truth & y_pred))
        results[key] = {
            "recall": caught / support,
            "caught": float(caught),
            "support": float(support),
        }
    return results


def compare_against_detector(
    node_ids: Sequence[str],
    gnn_pred: np.ndarray,
    detector_labelled: np.ndarray,
    ground_truth: GroundTruth,
) -> Dict[str, Any]:
    """Did the GNN beat its teacher, and where?

    The value of the model is not agreement with the detectors — it is the
    accounts the GNN flags that they missed AND that ground truth confirms.
    That set is `gnn_only_correct`; if it is empty, the GNN has learned to
    reproduce your heuristics and nothing more.

    Args:
        node_ids:          FeatureSet.node_ids
        gnn_pred:          [N] bool — GNN flagged this account
        detector_labelled: [N] bool — a detector flagged it
                           (FeatureSet.labelled_mask)
    """
    gnn_pred = np.asarray(gnn_pred).astype(bool).ravel()
    detector_labelled = np.asarray(detector_labelled).astype(bool).ravel()
    truth = ground_truth.labels_for(node_ids)

    gnn_only = gnn_pred & ~detector_labelled
    detector_only = detector_labelled & ~gnn_pred

    gnn_only_correct_mask = gnn_only & truth
    missed_by_both = truth & ~gnn_pred & ~detector_labelled

    return {
        "gnn_recall": float((gnn_pred & truth).sum() / max(int(truth.sum()), 1)),
        "detector_recall": float(
            (detector_labelled & truth).sum() / max(int(truth.sum()), 1)
        ),
        "gnn_only_flagged": int(gnn_only.sum()),
        "gnn_only_correct": int(gnn_only_correct_mask.sum()),
        "gnn_only_precision": float(
            gnn_only_correct_mask.sum() / max(int(gnn_only.sum()), 1)
        ),
        "detector_only_flagged": int(detector_only.sum()),
        "missed_by_both": int(missed_by_both.sum()),
        "ground_truth_support": int(truth.sum()),
        "gnn_only_correct_accounts": [
            node_ids[i] for i in np.flatnonzero(gnn_only_correct_mask)[:50]
        ],
    }
