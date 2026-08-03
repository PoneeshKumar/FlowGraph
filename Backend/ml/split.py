"""
Train/val/test splitting and feature scaling.

Two things live here because they share one rule: **fit on train only**. A
scaler fitted over the whole matrix leaks test statistics into training
features. It is the same defect class as the `isFraud` leak in
clean_paysim.py, just harder to see, because nothing about a column mean looks
label-related.

SPLIT BY TIME, NOT AT RANDOM
----------------------------
Payments are a time series. A random split lets the model learn from the future
to predict the past, which inflates every metric and does not survive
production. Splitting on each account's first observed activity is the closest
honest approximation available from this schema.

Its limit, stated plainly: FLOWS_TO aggregates are incremented on MERGE and
cannot be rewound, so a "test" account arrives carrying its full lifetime
including activity after the cutoff. The split therefore separates ACCOUNTS,
not time. That is not label leakage — the label is not in the features — but it
overstates how EARLY a mule would be caught, since production only ever has
history-to-date. Fixing it properly means rebuilding time-bounded features from
TRANSFER.ts or the Redis ZSETs, both of which keep per-transaction time.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SplitMasks:
    """Boolean node masks. Message passing still runs over ALL edges."""

    train: np.ndarray
    val: np.ndarray
    test: np.ndarray
    cutoffs: Tuple[float, float]

    def summary(self, labels: Optional[np.ndarray] = None) -> str:
        parts = [
            f"train={int(self.train.sum()):,}",
            f"val={int(self.val.sum()):,}",
            f"test={int(self.test.sum()):,}",
        ]
        if labels is not None:
            positives = labels.astype(bool)
            parts.append(
                f"positives {int((self.train & positives).sum()):,}/"
                f"{int((self.val & positives).sum()):,}/"
                f"{int((self.test & positives).sum()):,}"
            )
        return " ".join(parts)


def temporal_split(
    node_first_ts: np.ndarray,
    train_frac: float = 0.70,
    val_frac: float = 0.15,
) -> SplitMasks:
    """Split nodes chronologically on first observed activity.

    Args:
        node_first_ts: [num_nodes] unix seconds, NaN where unknown.
        train_frac:    Share of nodes in train.
        val_frac:      Share in validation; the remainder is test.

    Returns:
        SplitMasks covering every node exactly once. Nodes with an unknown
        timestamp go to train — they are almost always edgeless accounts that
        the model cannot be meaningfully evaluated on, and putting them in test
        would quietly depress the reported score.
    """
    if not 0.0 < train_frac < 1.0:
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    if not 0.0 <= val_frac < 1.0:
        raise ValueError(f"val_frac must be in [0, 1), got {val_frac}")
    if train_frac + val_frac >= 1.0:
        raise ValueError("train_frac + val_frac must leave room for test")

    times = np.asarray(node_first_ts, dtype="float64")
    num_nodes = times.shape[0]
    known = np.isfinite(times) & (times > 0)

    if not known.any():
        raise ValueError(
            "no usable node timestamps — a chronological split is impossible"
        )

    train_cut = float(np.quantile(times[known], train_frac))
    val_cut = float(np.quantile(times[known], train_frac + val_frac))

    train = np.zeros(num_nodes, dtype=bool)
    val = np.zeros(num_nodes, dtype=bool)
    test = np.zeros(num_nodes, dtype=bool)

    train[known & (times <= train_cut)] = True
    val[known & (times > train_cut) & (times <= val_cut)] = True
    test[known & (times > val_cut)] = True
    train[~known] = True

    logger.info(
        "Temporal split: train=%d val=%d test=%d (unknown-time -> train: %d)",
        int(train.sum()), int(val.sum()), int(test.sum()), int((~known).sum()),
    )
    return SplitMasks(train=train, val=val, test=test, cutoffs=(train_cut, val_cut))


class FeatureScaler:
    """Signed log1p followed by standardization, fitted on train nodes only.

    Why log first: the amount columns are heavy-tailed to an extreme degree —
    total_out_amount reaches 1.5e14 while pagerank_score peaks at 1.4e-4, a
    spread of ~1e18. Standardizing alone leaves that tail intact, so a handful
    of hub accounts still dominate every gradient. log1p compresses the tail;
    standardizing then puts all columns on a comparable scale.

    Signed (sign(x) * log1p(|x|)) because net_flow is legitimately negative —
    plain log1p would produce NaN for every account that received more than it
    sent.

    Monotonic throughout, so ordering within a column is preserved. Bounded
    columns (flow_ratio, the one-hots) pass through harmlessly.
    """

    def __init__(self, use_log: bool = True) -> None:
        self.use_log = use_log
        self.mean_: Optional[np.ndarray] = None
        self.std_: Optional[np.ndarray] = None

    @staticmethod
    def _signed_log1p(x: np.ndarray) -> np.ndarray:
        return np.sign(x) * np.log1p(np.abs(x))

    def fit(self, x: np.ndarray, train_mask: np.ndarray) -> "FeatureScaler":
        if train_mask.sum() == 0:
            raise ValueError("cannot fit a scaler on an empty train split")

        train_rows = x[train_mask]
        if self.use_log:
            train_rows = self._signed_log1p(train_rows)

        self.mean_ = train_rows.mean(axis=0)
        std = train_rows.std(axis=0)
        # A constant column would divide by zero. Leaving it at 1.0 maps it to
        # a constant 0 after centring, which is exactly what a dead column
        # should contribute.
        std[std == 0] = 1.0
        self.std_ = std
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.std_ is None:
            raise RuntimeError("scaler must be fitted before transform")
        out = self._signed_log1p(x) if self.use_log else x.astype("float64")
        out = (out - self.mean_) / self.std_
        return out.astype(np.float32)

    def fit_transform(self, x: np.ndarray, train_mask: np.ndarray) -> np.ndarray:
        return self.fit(x, train_mask).transform(x)


def random_split(
    num_nodes: int,
    train_frac: float = 0.70,
    val_frac: float = 0.15,
    seed: int = 42,
) -> SplitMasks:
    """Uniformly random node split. A DIAGNOSTIC, not a reporting split.

    This is deliberately the methodologically wrong choice for payment data:
    it lets the model learn from the future to predict the past. It exists so
    the temporal result can be interpreted. If random scores far higher than
    chronological, the gap measures distribution shift between early- and
    late-appearing accounts; if both are equally poor, the features simply do
    not separate the classes. Those call for completely different fixes, and
    without this comparison you cannot tell them apart.

    Never quote a number from here as the model's performance.
    """
    if train_frac + val_frac >= 1.0:
        raise ValueError("train_frac + val_frac must leave room for test")

    rng = np.random.default_rng(seed)
    order = rng.permutation(num_nodes)
    train_end = int(num_nodes * train_frac)
    val_end = int(num_nodes * (train_frac + val_frac))

    train = np.zeros(num_nodes, dtype=bool)
    val = np.zeros(num_nodes, dtype=bool)
    test = np.zeros(num_nodes, dtype=bool)
    train[order[:train_end]] = True
    val[order[train_end:val_end]] = True
    test[order[val_end:]] = True

    logger.warning(
        "RANDOM split in use — diagnostic only, not a reportable evaluation"
    )
    return SplitMasks(train=train, val=val, test=test, cutoffs=(float("nan"),) * 2)


def binary_labels_from_mask(positive_mask: np.ndarray) -> np.ndarray:
    """Ground-truth boolean -> int64 class indices for the loss."""
    return np.asarray(positive_mask, dtype=bool).astype(np.int64)
