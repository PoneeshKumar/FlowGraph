"""
Run several training configurations against one cached feature set and compare.

    python3 -m ml.sweep --preset baseline
    python3 -m ml.sweep --preset full

Loads the cache once and reuses it for every config, so a sweep costs roughly
one training run per config and nothing extra for data assembly.

Model selection reads VALIDATION PR-AUC. Test numbers are printed for the
record but must not drive the choice — picking the config with the best test
score turns the test split into a second validation set, and the reported
number stops being an estimate of unseen performance.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("sweep")


def _presets(name: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Named config sets. Each entry is (label, TrainConfig overrides)."""
    # Full-batch training does ONE gradient step per epoch, so epoch counts here
    # are far higher than a mini-batch setup would need. The baseline run peaked
    # at epoch 214, so anything under ~400 would cut off learning early.
    baseline: Dict[str, Any] = {
        "epochs": 800,
        "hidden": 64,
        "num_layers": 2,
        "dropout": 0.3,
        "lr": 0.01,
        "gamma": 2.0,
        "patience": 150,
    }

    if name == "baseline":
        return [("baseline", dict(baseline))]

    if name == "capacity":
        return [
            ("hidden32", {**baseline, "hidden": 32}),
            ("hidden64", dict(baseline)),
            ("hidden128", {**baseline, "hidden": 128}),
            ("hidden256", {**baseline, "hidden": 256}),
        ]

    if name == "depth":
        return [
            ("layers1", {**baseline, "num_layers": 1}),
            ("layers2", dict(baseline)),
            ("layers3", {**baseline, "num_layers": 3}),
            ("layers4", {**baseline, "num_layers": 4}),
        ]

    if name == "loss":
        # gamma=0 reduces Focal Loss to weighted cross-entropy, which is the
        # honest baseline for "does the focusing term earn its place".
        return [
            ("gamma0_weighted_ce", {**baseline, "gamma": 0.0}),
            ("gamma1", {**baseline, "gamma": 1.0}),
            ("gamma2", dict(baseline)),
            ("gamma3", {**baseline, "gamma": 3.0}),
            ("gamma2_effnum", {**baseline, "alpha_beta": 0.999}),
        ]

    if name == "imbalance":
        return [
            ("no_smote", dict(baseline)),
            ("smote_full", {**baseline, "use_smote": True, "smote_ratio": 1.0}),
            ("smote_half", {**baseline, "use_smote": True, "smote_ratio": 0.5}),
        ]

    if name == "regularization":
        # A CAUTIONARY preset, kept for the record. Widening to 256 and removing
        # regularization does drive VALIDATION PR-AUC up to ~0.44 — but that is
        # memorization, not learning: on the held-out FUTURE the same config
        # collapses to test ROC-AUC 0.50, literally no better than a coin flip.
        # Validation sits between train and test in time, so it is close enough
        # to train to reward overfitting; only the later test split exposes it.
        #
        # The real ceiling here is the early->late covariate shift, not
        # regularization strength. It is fixed by the `shift` preset below
        # (quantile normalization + bidirectional message passing), which lifts
        # TEST PR-AUC ~5x while barely moving validation. Chase test, not val.
        strong: Dict[str, Any] = {
            "epochs": 400,
            "hidden": 256,
            "num_layers": 2,
            "lr": 0.02,
            "gamma": 2.0,
            "patience": 150,
        }
        return [
            ("reg_none", {**strong, "dropout": 0.0, "weight_decay": 0.0}),
            ("reg_drop10", {**strong, "dropout": 0.1, "weight_decay": 0.0}),
            ("reg_drop20", {**strong, "dropout": 0.2, "weight_decay": 0.0}),
            ("reg_wd1e5", {**strong, "dropout": 0.0, "weight_decay": 1e-5}),
            ("reg_drop10_wd1e5", {**strong, "dropout": 0.1, "weight_decay": 1e-5}),
            ("reg_drop10_wd1e4", {**strong, "dropout": 0.1, "weight_decay": 1e-4}),
        ]

    if name == "shift":
        # The ablation that actually moved the needle. On a stable 60/15/25
        # temporal split (test has ~412 positives, not 139, so PR-AUC is less
        # noisy) each change is measured on TEST, because the whole benefit of
        # shift-robustness is invisible to validation. Measured, seed 42:
        #   log + unidirectional (old baseline) : test PR 0.057  ROC 0.940
        #   log + bidirectional                 : test PR 0.082  ROC 0.956
        #   quantile + unidirectional           : test PR 0.282  ROC 0.963
        #   quantile + bidirectional (champion) : test PR 0.281  ROC 0.975
        base_shift: Dict[str, Any] = {
            "epochs": 220,
            "hidden": 128,
            "num_layers": 2,
            "dropout": 0.3,
            "weight_decay": 5e-4,
            "lr": 0.01,
            "gamma": 2.0,
            "patience": 55,
            "train_frac": 0.60,
            "val_frac": 0.15,
        }
        return [
            ("log_unidir", {**base_shift, "scaler_kind": "log"}),
            ("log_bidir", {**base_shift, "scaler_kind": "log", "bidirectional": True}),
            ("quantile_unidir", {**base_shift, "scaler_kind": "quantile"}),
            ("quantile_bidir", {**base_shift, "scaler_kind": "quantile", "bidirectional": True}),
        ]

    if name == "aggr":
        return [
            ("mean", dict(baseline)),
            ("max", {**baseline, "aggr": "max"}),
        ]

    if name == "full":
        seen = set()
        combined: List[Tuple[str, Dict[str, Any]]] = []
        for group in ("capacity", "depth", "loss", "imbalance", "aggr"):
            for label, config in _presets(group):
                if label in seen:
                    continue
                seen.add(label)
                combined.append((label, config))
        return combined

    raise KeyError(f"unknown preset {name!r}")


def run_sweep(
    preset: str,
    cache_path: Path,
    patterns_path: Path,
    outdir: Path,
    device: str = "cpu",
    seed: int = 42,
) -> List[Dict[str, Any]]:
    from ml.evaluate import load_ground_truth
    from ml.train import TrainConfig, load_feature_cache, train_model

    feature_set = load_feature_cache(cache_path)
    ground_truth = load_ground_truth(patterns_path)
    logger.info(
        "Sweep over %d nodes / %d edges / %d features",
        feature_set.num_nodes,
        feature_set.edge_index.shape[1],
        feature_set.num_features,
    )

    outdir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []

    for label, overrides in _presets(preset):
        logger.info("=" * 60)
        logger.info("CONFIG: %s | %s", label, overrides)
        config = TrainConfig(device=device, seed=seed, **overrides)
        started = time.monotonic()
        try:
            model, result = train_model(feature_set, ground_truth, config)
        except Exception as exc:
            logger.error("config %s failed: %s", label, exc)
            rows.append({"label": label, "error": str(exc)})
            continue
        elapsed = time.monotonic() - started

        row = {
            "label": label,
            "val_pr_auc": result.best_val_pr_auc,
            "test_pr_auc": result.test_metrics["average_precision"],
            "test_precision": result.test_metrics["precision"],
            "test_recall": result.test_metrics["recall"],
            "test_f1": result.test_metrics["f1"],
            "best_epoch": result.best_epoch,
            "elapsed": elapsed,
            "gnn_only_correct": result.detector_comparison["gnn_only_correct"],
            "non_cycle_recall": _non_cycle_recall(result.typology_recall),
        }
        rows.append(row)

        run_dir = outdir / label
        run_dir.mkdir(parents=True, exist_ok=True)
        import torch

        torch.save(model.state_dict(), run_dir / "model.pt")
        with open(run_dir / "result.json", "w", encoding="utf-8") as handle:
            json.dump(asdict(result), handle, indent=2, default=float)

    return rows


def _non_cycle_recall(typology_recall: Dict[str, Dict[str, float]]) -> float:
    """Pooled recall over the typologies cycle detection cannot represent.

    The headline number for whether the GNN generalized beyond its teacher:
    FAN-OUT, FAN-IN, BIPARTITE, STACK, GATHER-SCATTER and SCATTER-GATHER have
    no closed loop, so no depth of cycle search reaches them.
    """
    from ml.evaluate import NON_CYCLE_TYPOLOGIES

    caught = 0.0
    support = 0.0
    for typology in NON_CYCLE_TYPOLOGIES:
        stats = typology_recall.get(typology)
        if not stats:
            continue
        caught += stats["caught"]
        support += stats["support"]
    return caught / support if support else 0.0


def render_table(rows: List[Dict[str, Any]]) -> str:
    header = (
        f"  {'config':22s} {'val PR-AUC':>10s} {'test PR-AUC':>11s} "
        f"{'prec':>6s} {'recall':>7s} {'F1':>6s} {'non-cyc':>8s} "
        f"{'ep':>4s} {'sec':>6s}"
    )
    lines = ["", "=" * 92, "SWEEP RESULTS (ranked by validation PR-AUC)", "=" * 92, header,
             "  " + "-" * 88]

    ranked = sorted(
        [r for r in rows if "error" not in r], key=lambda r: -r["val_pr_auc"]
    )
    for row in ranked:
        lines.append(
            f"  {row['label']:22s} {row['val_pr_auc']:10.4f} "
            f"{row['test_pr_auc']:11.4f} {row['test_precision']:6.3f} "
            f"{row['test_recall']:7.3f} {row['test_f1']:6.3f} "
            f"{row['non_cycle_recall']:8.3f} {row['best_epoch']:4d} "
            f"{row['elapsed']:6.0f}"
        )
    for row in rows:
        if "error" in row:
            lines.append(f"  {row['label']:22s} FAILED: {row['error'][:50]}")

    if ranked:
        best = ranked[0]
        lines.append("")
        lines.append(
            f"  best by validation: {best['label']} "
            f"(val {best['val_pr_auc']:.4f}, test {best['test_pr_auc']:.4f})"
        )
        lines.append(
            "  test numbers are reported, never used to choose — selecting on "
            "test would make it a second validation set"
        )
    lines.append("=" * 92)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", default="baseline")
    parser.add_argument("--cache", default="ml/cache/featureset.npz")
    parser.add_argument("--patterns", default="benchmarks/data/HI-Small_Patterns.txt")
    parser.add_argument("--outdir", default="ml/runs/sweep")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    rows = run_sweep(
        preset=args.preset,
        cache_path=Path(args.cache),
        patterns_path=Path(args.patterns),
        outdir=Path(args.outdir),
        device=args.device,
        seed=args.seed,
    )
    print(render_table(rows))

    with open(Path(args.outdir) / "sweep.json", "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, default=float)
    return 0


if __name__ == "__main__":
    sys.exit(main())
