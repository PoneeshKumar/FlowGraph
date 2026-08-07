"""
Score-averaged ensemble of several trained checkpoints.

WHY IT WORKS HERE (when it didn't before)
------------------------------------------
A seed-ensemble of the *full-batch* model was a wash (+0.007 test PR-AUC): those
members were near-identical, because full-batch training is deterministic given
the seed and the whole graph. Mini-batch training (ml/sampler.py) draws a fresh
random neighbourhood every step, so different seeds land on genuinely different
functions — and averaging their probabilities cuts the variance that survives.
Measured: three h256 mini-batch members at test PR-AUC ~0.67 each ensemble to
**0.70**, ROC 0.986 -> 0.991.

The cost is K forward passes. For a periodic whole-graph scoring pass (not
per-transaction) that is acceptable at K=3; the single-model champion
(v9_h256, test PR-AUC 0.66) is the cheaper option.

    python3 -m ml.ensemble --runs ml/runs/v9_h256 ml/runs/v9_h256_s1 \
        ml/runs/v9_h256_s7 --cache ml/cache/featureset_v4.npz --top 20
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import List

import numpy as np

logger = logging.getLogger("ensemble")


def _member_scores(run_dir: Path, feature_set) -> np.ndarray:
    """P(laundering) per node for one checkpoint, its own saved scaler applied.

    Each member re-applies the scaler it was trained with (they are identical in
    practice — the quantile scaler is data-dependent and deterministic — but
    using each member's own state keeps them independent).
    """
    import torch

    from ml.model import GraphSAGERiskClassifier
    from ml.predict import load_run

    result, scaler_state = load_run(run_dir)
    config = result["config"]
    if list(feature_set.feature_names) != list(result["feature_names"]):
        raise ValueError(
            f"{run_dir}: feature layout differs from the cache — column order is "
            f"part of the model contract. Score against the cache it trained on."
        )

    if scaler_state.get("kind") == "quantile":
        from ml.split import QuantileScaler

        x = QuantileScaler.from_state(scaler_state).transform(feature_set.x)
    else:
        mean = np.asarray(scaler_state["mean"], dtype="float64")
        std = np.asarray(scaler_state["std"], dtype="float64")
        x = feature_set.x.astype("float64")
        if scaler_state.get("use_log", True):
            x = np.sign(x) * np.log1p(np.abs(x))
        x = ((x - mean) / std).astype(np.float32)

    model = GraphSAGERiskClassifier(
        in_channels=feature_set.num_features, hidden=config["hidden"], num_classes=2,
        num_layers=config["num_layers"], dropout=config["dropout"],
        aggr=config.get("aggr", "mean"), bidirectional=config.get("bidirectional", False),
    )
    model.load_state_dict(torch.load(run_dir / "model.pt", map_location="cpu"))
    model.eval()
    with torch.no_grad():
        edge_index = torch.from_numpy(feature_set.edge_index).long()
        logits = model(torch.from_numpy(x), edge_index)
        return torch.softmax(logits.float(), dim=-1)[:, 1].numpy()


def ensemble_scores(run_dirs: List[Path], feature_set) -> np.ndarray:
    """Mean of the members' P(laundering). Averaging probabilities (not logits)
    keeps every member on the same [0, 1] scale before combining."""
    if not run_dirs:
        raise ValueError("an ensemble needs at least one member")
    acc = None
    for run_dir in run_dirs:
        s = _member_scores(run_dir, feature_set)
        acc = s if acc is None else acc + s
        logger.info("scored member %s", run_dir.name)
    return acc / len(run_dirs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True)
    parser.add_argument("--cache", default="ml/cache/featureset_v4.npz")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--patterns", default="benchmarks/data/HI-Small_Patterns.txt")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    from ml.predict import EXPLAINABLE, risk_level
    from ml.train import load_feature_cache

    feature_set = load_feature_cache(Path(args.cache))
    scores = ensemble_scores([Path(r) for r in args.runs], feature_set)

    truth = np.zeros(len(scores), dtype=bool)
    if Path(args.patterns).exists():
        from ml.evaluate import load_ground_truth

        truth = load_ground_truth(args.patterns).labels_for(feature_set.node_ids)

    order = np.argsort(-scores)[: args.top]
    print(f"\nTOP {args.top} BY ENSEMBLE RISK ({len(args.runs)} members)")
    hits = 0
    for rank, idx in enumerate(order, 1):
        hits += bool(truth[idx])
        print(
            f"{rank:3d}. {feature_set.node_ids[idx][:16]}…  score {scores[idx]:.4f}  "
            f"{risk_level(float(scores[idx])):8s} {'CONFIRMED' if truth[idx] else ''}"
        )
    print(f"precision@{args.top}: {hits}/{args.top} = {hits / args.top:.1%} "
          f"(base rate {truth.mean():.3%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
