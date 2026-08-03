"""
Score accounts with a trained checkpoint.

    python3 -m ml.predict --run ml/runs/baseline_long --top 25
    python3 -m ml.predict --run ml/runs/baseline_long --account <id>

Rebuilds features from the live stores (or a cache), applies the SAVED scaler
statistics, and runs the model. Using the saved statistics rather than
re-fitting is essential: re-fitting on inference data would both leak and shift
the inputs away from what the weights were trained on.

Every score ships with a written reason. That is a project convention and a
regulatory requirement — `Risk scores are always produced with a written
explanation. Never emit a bare numeric score.` The explanation here is derived
from the feature values that drove the score, not from the model's internals;
a full attribution method (GNNExplainer, integrated gradients) is the next step
if per-edge evidence is needed.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("predict")

# Bands mapping P(laundering) onto the four risk levels in the schema. The
# model is trained binary; these thresholds are a presentation layer, and
# should be recalibrated whenever the operating point changes.
RISK_BANDS: Tuple[Tuple[float, str], ...] = (
    (0.90, "critical"),
    (0.70, "high"),
    (0.40, "medium"),
    (0.00, "low"),
)

# Features worth naming in an explanation, with a human phrasing for each.
EXPLAINABLE: Dict[str, str] = {
    "flow_ratio": "forwards {value:.0%} of everything it touches",
    "tx_per_counterparty": "averages {value:.1f} transactions per counterparty",
    "avg_out_amount": "sends an average of {value:,.0f} per transaction",
    "out_degree": "pays {value:,.0f} distinct counterparties",
    "in_degree": "receives from {value:,.0f} distinct counterparties",
    "burst_1h_24h": "concentrates {value:.0%} of a day's activity into one hour",
    "burst_24h_7d": "concentrates {value:.0%} of a week's activity into one day",
    "pagerank_score": "sits at PageRank {value:.2e} in the flow graph",
    "community_size": "belongs to a {value:,.0f}-account community",
    "self_loop_count": "made {value:,.0f} transfers to itself",
}


def risk_level(score: float) -> str:
    for threshold, level in RISK_BANDS:
        if score >= threshold:
            return level
    return "low"


def explain(
    score: float,
    row: np.ndarray,
    feature_names: List[str],
    percentiles: Dict[str, np.ndarray],
    top_k: int = 3,
) -> str:
    """A written reason, built from the account's most unusual features.

    Unusual is measured against the population, not against zero: an account
    paying 3 counterparties is unremarkable on its own and notable only if most
    accounts pay one. Percentile rank makes that comparison explicit.
    """
    ranked: List[Tuple[float, str]] = []
    for name, phrasing in EXPLAINABLE.items():
        if name not in feature_names:
            continue
        idx = feature_names.index(name)
        value = float(row[idx])
        reference = percentiles.get(name)
        if reference is None or reference.size == 0:
            continue

        # Zero is the MODE of most of these columns, not an anomaly — the
        # majority of accounts never self-transfer, and plenty are pure
        # receivers with no outgoing degree. Reporting "sends an average of 0
        # per transaction" as a reason is worse than saying nothing, so an
        # absent behaviour is never evidence.
        if value <= 0.0:
            continue

        # Only unusually HIGH values explain a risk score. A below-median
        # value is what most of the population looks like.
        rank = float((reference <= value).mean())
        if rank < 0.90:
            continue
        ranked.append((rank, phrasing.format(value=value)))

    ranked.sort(reverse=True)
    reasons = [text for _, text in ranked[:top_k]]
    level = risk_level(score)

    if not reasons:
        return (
            f"Risk {level} (p={score:.3f}). No individual feature is unusual; "
            f"the score comes from the account's neighbourhood rather than its "
            f"own behaviour."
        )
    return f"Risk {level} (p={score:.3f}). This account " + "; ".join(reasons) + "."


def load_run(run_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    with open(run_dir / "result.json", encoding="utf-8") as handle:
        result = json.load(handle)
    if not result.get("scaler"):
        raise ValueError(
            f"{run_dir} has no saved scaler statistics — it predates scaler "
            f"persistence and cannot be used for inference. Retrain it."
        )
    return result, result["scaler"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="a directory under ml/runs/")
    parser.add_argument("--cache", default="ml/cache/featureset.npz")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--account", default=None, help="score one account id")
    parser.add_argument("--patterns", default="benchmarks/data/HI-Small_Patterns.txt")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    import torch

    from ml.model import GraphSAGERiskClassifier
    from ml.train import load_feature_cache

    run_dir = Path(args.run)
    result, scaler_state = load_run(run_dir)
    config = result["config"]

    feature_set = load_feature_cache(Path(args.cache))
    if list(feature_set.feature_names) != list(result["feature_names"]):
        raise ValueError(
            "feature layout has changed since this model was trained — column "
            "order is part of the model's contract. Retrain, or score against "
            "the cache the model was trained on."
        )

    mean = np.asarray(scaler_state["mean"], dtype="float64")
    std = np.asarray(scaler_state["std"], dtype="float64")
    x = feature_set.x.astype("float64")
    if scaler_state.get("use_log", True):
        x = np.sign(x) * np.log1p(np.abs(x))
    x = ((x - mean) / std).astype(np.float32)

    model = GraphSAGERiskClassifier(
        in_channels=feature_set.num_features,
        hidden=config["hidden"],
        num_classes=2,
        num_layers=config["num_layers"],
        dropout=config["dropout"],
        aggr=config.get("aggr", "mean"),
    )
    model.load_state_dict(torch.load(run_dir / "model.pt", map_location="cpu"))
    model.eval()

    with torch.no_grad():
        logits = model(
            torch.from_numpy(x), torch.from_numpy(feature_set.edge_index).long()
        )
        scores = torch.softmax(logits.float(), dim=-1)[:, 1].numpy()

    percentiles = {
        name: feature_set.x[:, i]
        for i, name in enumerate(feature_set.feature_names)
        if name in EXPLAINABLE
    }

    ground_truth = None
    if Path(args.patterns).exists():
        from ml.evaluate import load_ground_truth

        ground_truth = load_ground_truth(args.patterns)
    truth = (
        ground_truth.labels_for(feature_set.node_ids)
        if ground_truth
        else np.zeros(len(scores), dtype=bool)
    )

    if args.account:
        if args.account not in feature_set.node_ids:
            logger.error("account %s not in the feature set", args.account)
            return 1
        idx = feature_set.node_ids.index(args.account)
        print()
        print(f"account   : {args.account}")
        print(f"score     : {scores[idx]:.4f}")
        print(f"level     : {risk_level(float(scores[idx]))}")
        print(f"in truth  : {bool(truth[idx])}")
        print(
            "reason    : "
            + explain(
                float(scores[idx]), feature_set.x[idx],
                feature_set.feature_names, percentiles,
            )
        )
        return 0

    order = np.argsort(-scores)[: args.top]
    print()
    print("=" * 78)
    print(f"TOP {args.top} ACCOUNTS BY RISK SCORE")
    print("=" * 78)
    hits = 0
    for rank, idx in enumerate(order, start=1):
        confirmed = bool(truth[idx])
        hits += confirmed
        print(
            f"{rank:3d}. {feature_set.node_ids[idx][:16]}…  "
            f"score {scores[idx]:.4f}  {risk_level(float(scores[idx])):8s} "
            f"{'CONFIRMED' if confirmed else ''}"
        )
        print(
            "     "
            + explain(
                float(scores[idx]), feature_set.x[idx],
                feature_set.feature_names, percentiles,
            )
        )
    print("=" * 78)
    if ground_truth:
        print(
            f"precision@{args.top}: {hits}/{args.top} = {hits / args.top:.1%} "
            f"(base rate {truth.mean():.3%})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
