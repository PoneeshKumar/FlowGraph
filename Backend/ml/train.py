"""
Train the GraphSAGE risk classifier.

    python3 -m ml.train                       # build/reuse cache, train, evaluate
    python3 -m ml.train --refresh-cache       # rebuild features from the stores
    python3 -m ml.train --epochs 300 --hidden 128 --smote
    python3 -m ml.train --cache-only          # build the cache and stop

Building the feature set touches Neo4j, Redis and Postgres and takes several
minutes, so it is cached to an .npz. Hyperparameter runs reuse it.

WHAT IS BEING LEARNED
---------------------
Binary node classification: is this account part of a laundering pattern?
Labels are the IBM AML ground truth (370 labelled attempts across 8
typologies), NOT the risk_flags weak labels. The weak labels are 100% precise
but cover 124 accounts against ground truth's 3,170, and training a model to
imitate the cycle detector would inherit its blind spots — recall on the six
typologies with no closed loop is the whole point.

Message passing runs over every edge; only the loss and the metrics respect the
split masks. Using a test node's FEATURES is legitimate (they exist at
inference time in production too); using its LABEL is not, and the masks are
what prevent it.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("train")

DEFAULT_CACHE = "ml/cache/featureset.npz"
DEFAULT_OUTDIR = "ml/runs"
POSITIVE_CLASS = 1


# ---------------------------------------------------------------------------
# feature cache
# ---------------------------------------------------------------------------


def save_feature_cache(feature_set: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        node_ids=np.asarray(feature_set.node_ids, dtype=object),
        x=feature_set.x,
        y=feature_set.y,
        labelled_mask=feature_set.labelled_mask,
        edge_index=feature_set.edge_index,
        edge_weight=feature_set.edge_weight,
        feature_names=np.asarray(feature_set.feature_names, dtype=object),
        node_first_ts=(
            feature_set.node_first_ts
            if feature_set.node_first_ts is not None
            else np.zeros(0)
        ),
    )
    logger.info("Feature cache written: %s", path)


def load_feature_cache(path: Path) -> Any:
    from ml.features import FeatureSet

    blob = np.load(path, allow_pickle=True)
    first_ts = blob["node_first_ts"]
    return FeatureSet(
        node_ids=[str(v) for v in blob["node_ids"]],
        x=blob["x"],
        y=blob["y"],
        labelled_mask=blob["labelled_mask"],
        edge_index=blob["edge_index"],
        edge_weight=blob["edge_weight"],
        feature_names=[str(v) for v in blob["feature_names"]],
        node_first_ts=first_ts if first_ts.size else None,
    )


async def build_feature_set(
    window_days: int, anchor_percentile: float, export_timeout: float
) -> Any:
    """Assemble a FeatureSet from the live stores."""
    from datetime import datetime, timezone

    from db.neo4j import Neo4jClient
    from db.redis import RedisClient
    from ml.features import FeatureBuilder

    neo4j_client = Neo4jClient()
    await neo4j_client.initialize()
    redis_client = RedisClient()
    await redis_client.initialize()

    postgres_client = None
    try:
        from db.postgres import PostgresClient

        postgres_client = PostgresClient()
        await postgres_client.initialize()
    except Exception as exc:
        logger.warning("Postgres unavailable (%s) — weak labels will be empty", exc)
        postgres_client = None

    try:
        # Historical data: a wall-clock-anchored window would treat every edge
        # as stale and zero the Redis features. The quantile skips the sparse
        # tail — see get_flows_to_timestamp.
        anchor = await neo4j_client.get_flows_to_timestamp(percentile=anchor_percentile)
        reference = (
            datetime.fromtimestamp(anchor, tz=timezone.utc) if anchor else None
        )
        logger.info("Anchoring feature windows at %s", reference)

        builder = FeatureBuilder(neo4j_client, redis_client, postgres_client)
        return await builder.build(
            window_days=window_days,
            reference_time=reference,
            export_timeout_seconds=export_timeout,
        )
    finally:
        await neo4j_client.close()
        await redis_client.close()
        if postgres_client is not None:
            await postgres_client.close()


# ---------------------------------------------------------------------------
# config + results
# ---------------------------------------------------------------------------


@dataclass
class TrainConfig:
    epochs: int = 200
    hidden: int = 64
    num_layers: int = 2
    dropout: float = 0.3
    lr: float = 0.01
    weight_decay: float = 5e-4
    gamma: float = 2.0
    alpha_beta: Optional[float] = None      # None = inverse frequency
    aggr: str = "mean"
    patience: int = 30
    seed: int = 42
    device: str = "cpu"
    use_smote: bool = False
    smote_ratio: float = 1.0
    use_log_scaling: bool = True
    train_frac: float = 0.70
    val_frac: float = 0.15
    label_source: str = "ground_truth"
    # "temporal" is the reportable split. "random" is a diagnostic that
    # deliberately breaks chronology — see ml.split.random_split.
    split_strategy: str = "temporal"


@dataclass
class TrainResult:
    config: Dict[str, Any]
    best_epoch: int
    best_val_pr_auc: float
    threshold: float
    val_metrics: Dict[str, Any]
    test_metrics: Dict[str, Any]
    typology_recall: Dict[str, Dict[str, float]]
    detector_comparison: Dict[str, Any]
    history: List[Dict[str, float]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    split_summary: str = ""
    model_summary: List[str] = field(default_factory=list)
    feature_names: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# training
# ---------------------------------------------------------------------------


def train_model(
    feature_set: Any,
    ground_truth: Any,
    config: TrainConfig,
) -> Tuple[Any, TrainResult]:
    import torch

    from ml.evaluate import (
        compare_against_detector,
        fraud_metrics,
        recall_by_typology,
    )
    from ml.losses import FocalLoss, class_balanced_alpha
    from ml.model import GraphSAGERiskClassifier, pick_device
    from ml.split import FeatureScaler, temporal_split

    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    device = pick_device(config.device)
    logger.info("Device: %s", device)

    # ---- labels ---------------------------------------------------------
    if config.label_source == "ground_truth":
        positives = ground_truth.labels_for(feature_set.node_ids)
    else:
        positives = feature_set.y > 0
    labels = positives.astype(np.int64)
    logger.info(
        "Labels: %d positive of %d (%.3f%%)",
        int(positives.sum()), len(labels), 100 * positives.mean(),
    )

    # ---- split ----------------------------------------------------------
    if config.split_strategy == "random":
        from ml.split import random_split

        masks = random_split(
            len(labels),
            train_frac=config.train_frac,
            val_frac=config.val_frac,
            seed=config.seed,
        )
    else:
        if feature_set.node_first_ts is None:
            raise ValueError(
                "FeatureSet has no node_first_ts — a chronological split needs "
                "one. Rebuild the cache with --refresh-cache."
            )
        masks = temporal_split(
            feature_set.node_first_ts,
            train_frac=config.train_frac,
            val_frac=config.val_frac,
        )
    split_summary = masks.summary(positives)
    logger.info("Split: %s", split_summary)

    for name, mask in (("train", masks.train), ("val", masks.val), ("test", masks.test)):
        if int((mask & positives).sum()) == 0:
            raise ValueError(f"{name} split contains no positives — cannot proceed")

    # ---- scaling, fitted on TRAIN ONLY ----------------------------------
    scaler = FeatureScaler(use_log=config.use_log_scaling)
    x_scaled = scaler.fit_transform(feature_set.x, masks.train)
    logger.info(
        "Scaled features: max|x| %.4g (was %.4g)",
        float(np.abs(x_scaled).max()), float(np.abs(feature_set.x).max()),
    )

    x = torch.from_numpy(x_scaled).to(device)
    edge_index = torch.from_numpy(feature_set.edge_index).long().to(device)
    y = torch.from_numpy(labels).to(device)
    train_mask = torch.from_numpy(masks.train).to(device)
    val_mask = torch.from_numpy(masks.val).to(device)

    # ---- model ----------------------------------------------------------
    model = GraphSAGERiskClassifier(
        in_channels=feature_set.num_features,
        hidden=config.hidden,
        num_classes=2,
        num_layers=config.num_layers,
        dropout=config.dropout,
        aggr=config.aggr,
    ).to(device)
    for line in model.describe():
        logger.info("  %s", line)

    alpha = class_balanced_alpha(
        y[train_mask].cpu(), num_classes=2, beta=config.alpha_beta
    )
    logger.info("Focal alpha (per class): %s", [round(float(a), 4) for a in alpha])
    criterion = FocalLoss(gamma=config.gamma, alpha=alpha.to(device))
    optimizer = torch.optim.Adam(
        model.parameters(), lr=config.lr, weight_decay=config.weight_decay
    )

    val_truth = positives[masks.val]
    best_val = -1.0
    best_epoch = -1
    best_state: Optional[Dict[str, Any]] = None
    history: List[Dict[str, float]] = []
    epochs_without_gain = 0
    started = time.monotonic()

    for epoch in range(1, config.epochs + 1):
        model.train()
        optimizer.zero_grad()

        embeddings = model.encode(x, edge_index)
        train_embeddings = embeddings[train_mask]
        train_labels = y[train_mask]

        if config.use_smote:
            # AFTER the convolutions, never before: a synthetic node has no
            # edges and so cannot participate in message passing at all.
            from ml.imbalance import smote_embeddings

            train_embeddings, train_labels = smote_embeddings(
                train_embeddings,
                train_labels,
                target_ratio=config.smote_ratio,
            )

        loss = criterion(model.classify(train_embeddings), train_labels)
        loss.backward()
        optimizer.step()

        model.eval()
        with torch.no_grad():
            scores = torch.softmax(model(x, edge_index).float(), dim=-1)[
                :, POSITIVE_CLASS
            ]
        val_scores = scores[val_mask].cpu().numpy()
        val_pr_auc = fraud_metrics(
            val_truth, val_scores >= 0.5, val_scores
        ).average_precision

        history.append(
            {"epoch": epoch, "loss": float(loss.item()), "val_pr_auc": float(val_pr_auc)}
        )

        if val_pr_auc > best_val:
            best_val = float(val_pr_auc)
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            epochs_without_gain = 0
        else:
            epochs_without_gain += 1

        if epoch % 10 == 0 or epoch == 1:
            logger.info(
                "epoch %3d | loss %.5f | val PR-AUC %.4f | best %.4f @%d",
                epoch, loss.item(), val_pr_auc, best_val, best_epoch,
            )

        if epochs_without_gain >= config.patience:
            logger.info(
                "Early stop at epoch %d (%d without improvement)",
                epoch, config.patience,
            )
            break

    elapsed = time.monotonic() - started
    if best_state is not None:
        model.load_state_dict(best_state)
    model.to(device)

    # ---- final scoring --------------------------------------------------
    model.eval()
    with torch.no_grad():
        scores_all = (
            torch.softmax(model(x, edge_index).float(), dim=-1)[:, POSITIVE_CLASS]
            .cpu()
            .numpy()
        )

    # Threshold picked on VALIDATION, never on test — choosing it on test would
    # report the best threshold in hindsight rather than one you could have set.
    threshold = _best_f1_threshold(val_truth, scores_all[masks.val])
    logger.info("Threshold chosen on validation: %.4f", threshold)

    val_metrics = fraud_metrics(
        val_truth, scores_all[masks.val] >= threshold, scores_all[masks.val]
    )
    test_truth = positives[masks.test]
    test_scores = scores_all[masks.test]
    test_metrics = fraud_metrics(test_truth, test_scores >= threshold, test_scores)

    predictions = scores_all >= threshold
    typology = recall_by_typology(feature_set.node_ids, predictions, ground_truth)
    comparison = compare_against_detector(
        feature_set.node_ids, predictions, feature_set.labelled_mask, ground_truth
    )

    result = TrainResult(
        config=asdict(config),
        best_epoch=best_epoch,
        best_val_pr_auc=best_val,
        threshold=float(threshold),
        val_metrics=val_metrics.as_dict(),
        test_metrics=test_metrics.as_dict(),
        typology_recall=typology,
        detector_comparison=comparison,
        history=history,
        elapsed_seconds=elapsed,
        split_summary=split_summary,
        model_summary=model.describe(),
        feature_names=list(feature_set.feature_names),
    )
    return model, result


def _best_f1_threshold(truth: np.ndarray, scores: np.ndarray) -> float:
    """Threshold maximizing F1 on the given split.

    Swept over the precision-recall curve's own thresholds rather than a fixed
    grid, so the choice adapts to however the scores are distributed — at 0.6%
    prevalence they cluster hard near zero and a linspace would miss the useful
    region entirely.
    """
    from sklearn.metrics import precision_recall_curve

    if truth.sum() == 0 or truth.all():
        return 0.5

    precision, recall, thresholds = precision_recall_curve(truth, scores)
    # precision_recall_curve returns one fewer threshold than points.
    f1 = np.divide(
        2 * precision[:-1] * recall[:-1],
        precision[:-1] + recall[:-1],
        out=np.zeros_like(precision[:-1]),
        where=(precision[:-1] + recall[:-1]) > 0,
    )
    if f1.size == 0:
        return 0.5
    return float(thresholds[int(np.argmax(f1))])


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def render_result(result: TrainResult) -> str:
    width = 74
    out = ["", "=" * width, "TRAINING RESULT", "=" * width]
    out.append(f"  elapsed            : {result.elapsed_seconds:.1f}s")
    out.append(f"  best epoch         : {result.best_epoch}")
    out.append(f"  best val PR-AUC    : {result.best_val_pr_auc:.4f}")
    out.append(f"  threshold (on val) : {result.threshold:.4f}")
    out.append(f"  split              : {result.split_summary}")
    out.append("")
    for label, metrics in (("VALIDATION", result.val_metrics), ("TEST", result.test_metrics)):
        out.append(f"  {label}")
        out.append(
            f"    PR-AUC   {metrics['average_precision']:.4f}   "
            f"ROC-AUC  {metrics['roc_auc'] if metrics['roc_auc'] is None else round(metrics['roc_auc'], 4)}"
        )
        out.append(
            f"    precision {metrics['precision']:.4f}  "
            f"recall {metrics['recall']:.4f}  f1 {metrics['f1']:.4f}"
        )
        out.append(
            f"    TP {metrics['true_positives']:,}  FP {metrics['false_positives']:,}  "
            f"FN {metrics['false_negatives']:,}  support {metrics['support']:,}"
        )
        out.append(
            f"    prevalence {metrics['prevalence']:.4%}  "
            f"(accuracy {metrics['accuracy']:.4f} — meaningless here)"
        )
        out.append("")

    out.append("  RECALL BY TYPOLOGY (whole graph, at the chosen threshold)")
    out.append("    typology            recall   caught / support")
    for typology, stats in sorted(
        result.typology_recall.items(), key=lambda kv: -kv[1]["recall"]
    ):
        out.append(
            f"    {typology:18s} {stats['recall']:6.1%}   "
            f"{int(stats['caught']):,} / {int(stats['support']):,}"
        )

    comparison = result.detector_comparison
    out.append("")
    out.append("  VS THE DETECTORS")
    out.append(f"    GNN recall           : {comparison['gnn_recall']:.1%}")
    out.append(f"    detector recall      : {comparison['detector_recall']:.1%}")
    out.append(
        f"    GNN-only correct     : {comparison['gnn_only_correct']:,} "
        f"of {comparison['gnn_only_flagged']:,} flagged "
        f"(precision {comparison['gnn_only_precision']:.1%})"
    )
    out.append(f"    missed by both       : {comparison['missed_by_both']:,}")
    out.append("=" * width)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--outdir", default=DEFAULT_OUTDIR)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--patterns", default="benchmarks/data/HI-Small_Patterns.txt")

    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--dropout", type=float, default=0.3)
    parser.add_argument("--lr", type=float, default=0.01)
    parser.add_argument("--weight-decay", type=float, default=5e-4)
    parser.add_argument("--gamma", type=float, default=2.0)
    parser.add_argument("--alpha-beta", type=float, default=None)
    parser.add_argument("--aggr", default="mean", choices=["mean", "max", "sum"])
    parser.add_argument("--patience", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--smote", action="store_true")
    parser.add_argument("--smote-ratio", type=float, default=1.0)
    parser.add_argument("--no-log-scaling", action="store_true")
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--val-frac", type=float, default=0.15)
    parser.add_argument(
        "--label-source", default="ground_truth", choices=["ground_truth", "weak"]
    )
    parser.add_argument(
        "--split", default="temporal", choices=["temporal", "random"],
        help="'random' is a diagnostic only — it breaks chronology and its "
             "numbers must not be reported as model performance",
    )

    parser.add_argument("--window-days", type=int, default=2_000)
    parser.add_argument("--anchor-percentile", type=float, default=0.999)
    parser.add_argument("--export-timeout", type=float, default=1800.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    cache_path = Path(args.cache)
    if args.refresh_cache or not cache_path.exists():
        logger.info("Building feature set from the live stores …")
        feature_set = asyncio.run(
            build_feature_set(
                args.window_days, args.anchor_percentile, args.export_timeout
            )
        )
        save_feature_cache(feature_set, cache_path)
    else:
        logger.info("Loading cached feature set: %s", cache_path)
        feature_set = load_feature_cache(cache_path)

    logger.info(
        "Feature set: %d nodes, %d edges, %d features",
        feature_set.num_nodes,
        feature_set.edge_index.shape[1],
        feature_set.num_features,
    )
    if args.cache_only:
        return 0

    patterns_path = Path(args.patterns)
    if not patterns_path.exists():
        logger.error("Ground truth not found: %s", patterns_path)
        return 1

    from ml.evaluate import load_ground_truth

    ground_truth = load_ground_truth(patterns_path)

    config = TrainConfig(
        epochs=args.epochs,
        hidden=args.hidden,
        num_layers=args.num_layers,
        dropout=args.dropout,
        lr=args.lr,
        weight_decay=args.weight_decay,
        gamma=args.gamma,
        alpha_beta=args.alpha_beta,
        aggr=args.aggr,
        patience=args.patience,
        seed=args.seed,
        device=args.device,
        use_smote=args.smote,
        smote_ratio=args.smote_ratio,
        use_log_scaling=not args.no_log_scaling,
        train_frac=args.train_frac,
        val_frac=args.val_frac,
        label_source=args.label_source,
        split_strategy=args.split,
    )

    model, result = train_model(feature_set, ground_truth, config)
    print(render_result(result))

    run_name = args.run_name or time.strftime("run_%Y%m%d_%H%M%S")
    outdir = Path(args.outdir) / run_name
    outdir.mkdir(parents=True, exist_ok=True)

    import torch

    torch.save(model.state_dict(), outdir / "model.pt")
    with open(outdir / "result.json", "w", encoding="utf-8") as handle:
        json.dump(asdict(result), handle, indent=2, default=float)
    logger.info("Saved model and metrics to %s", outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
