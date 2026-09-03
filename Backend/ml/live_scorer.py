"""Cached-model inductive scorer for live per-event scoring.

`ml.ensemble._member_scores` reloads a checkpoint from disk on every call — fine
for a one-shot batch pass, far too slow to run per payment event. ``LiveScorer``
loads each member's config, scaler and torch model **once** at construction and
keeps them resident, so scoring a neighborhood is just scaler-transform + forward
pass + average. It reproduces the ensemble scoring path exactly (same per-member
scaler, same softmax column), just without the per-call disk load.

Inductive by construction (SAGEConv): the resident weights score any FeatureSet
regardless of which/how many accounts it contains, so a fresh neighborhood
assembled per event needs no retraining.
"""
import logging
from pathlib import Path
from typing import List, Sequence

import numpy as np

logger = logging.getLogger("ml.live_scorer")


def _apply_scaler(scaler_state: dict, x: np.ndarray) -> np.ndarray:
    """Apply a checkpoint's saved scaler exactly as at training time (never re-fit).

    Mirrors ml.ensemble._member_scores / ml.predict so live scores match batch."""
    if scaler_state.get("kind") == "quantile":
        from ml.split import QuantileScaler
        return QuantileScaler.from_state(scaler_state).transform(x)
    mean = np.asarray(scaler_state["mean"], dtype="float64")
    std = np.asarray(scaler_state["std"], dtype="float64")
    xs = x.astype("float64")
    if scaler_state.get("use_log", True):
        xs = np.sign(xs) * np.log1p(np.abs(xs))
    return ((xs - mean) / std).astype(np.float32)


class _Member:
    """One resident checkpoint: config, scaler state, and an eval-mode model."""

    def __init__(self, run_dir: Path):
        import torch
        from ml.model import GraphSAGERiskClassifier
        from ml.predict import load_run

        result, scaler_state = load_run(run_dir)
        cfg = result["config"]
        self.name = run_dir.name
        self.feature_names = list(result["feature_names"])
        self.scaler_state = scaler_state
        model = GraphSAGERiskClassifier(
            in_channels=len(self.feature_names), hidden=cfg["hidden"], num_classes=2,
            num_layers=cfg["num_layers"], dropout=cfg["dropout"],
            aggr=cfg.get("aggr", "mean"), bidirectional=cfg.get("bidirectional", False),
        )
        model.load_state_dict(torch.load(run_dir / "model.pt", map_location="cpu"))
        model.eval()
        self.model = model

    def score(self, feature_set) -> np.ndarray:
        import torch
        if list(feature_set.feature_names) != self.feature_names:
            raise ValueError(
                f"{self.name}: feature layout differs from the model contract — "
                "column order is fixed; assemble with the trained feature set."
            )
        x = _apply_scaler(self.scaler_state, feature_set.x)
        with torch.no_grad():
            logits = self.model(
                torch.from_numpy(x), torch.from_numpy(feature_set.edge_index).long()
            )
            return torch.softmax(logits.float(), dim=-1)[:, 1].numpy()


class LiveScorer:
    """Resident champion (+ optional ensemble members) scored over a FeatureSet."""

    def __init__(self, run_dir: str, ensemble_runs: Sequence[str] = ()):
        # Ensemble members are optional artifacts (ml/runs is gitignored) — skip any
        # that aren't on disk so live scoring degrades to the champion, never crashes.
        paths: List[Path] = [Path(run_dir)]
        paths += [Path(p) for p in ensemble_runs if Path(p).exists()]
        self.members = [_Member(p) for p in paths]
        logger.info("LiveScorer resident with %d model(s): %s",
                    len(self.members), ", ".join(m.name for m in self.members))

    def score(self, feature_set) -> np.ndarray:
        """Mean P(laundering) per node, aligned to ``feature_set.node_ids``."""
        acc = None
        for m in self.members:
            s = m.score(feature_set)
            acc = s if acc is None else acc + s
        return acc / len(self.members)
