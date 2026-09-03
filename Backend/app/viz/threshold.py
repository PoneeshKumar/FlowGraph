"""The GNN mark cutoff — the model's own validation-tuned threshold.

The champion checkpoint stores the threshold that maximised validation F1
(``result.json['threshold']``, ≈0.74 for v10_L3). Marking at a hardcoded 0.5
instead — far below that operating point — is what floods the results with false
positives (whole graph: 7,074 FP / precision 0.27 at 0.5, vs 474 FP / precision
0.79 at 0.738). This loads the stored value once, falling back to the config
default if the run isn't present.
"""
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from app.core.config import settings
from ml.predict import load_run

logger = logging.getLogger("viz.threshold")

# Slider bounds for the viewer — a sensible precision/recall exploration range.
MIN_CUTOFF = 0.50
MAX_CUTOFF = 0.95

_cached: Optional[float] = None


def invalidate() -> None:
    """Drop the cached threshold. Call after a run dir is hot-swapped (a
    retrained checkpoint dropped in place) so the next model_threshold() call
    re-reads result.json instead of serving the value loaded at first use."""
    global _cached
    _cached = None


def model_threshold() -> float:
    """The model's tuned mark cutoff, cached. Falls back to MARK_GNN_THRESHOLD."""
    global _cached
    if _cached is not None:
        return _cached
    fallback = float(getattr(settings, "MARK_GNN_THRESHOLD", 0.5))
    try:
        # load_run also validates the run has a persisted scaler, so a run dir
        # that's unusable for real inference is treated the same as a missing one.
        result, _scaler = load_run(Path(settings.GNN_RUN_DIR))
        thr = float(result["threshold"])
        # keep it inside the slider range so the default is always reachable
        _cached = max(MIN_CUTOFF, min(MAX_CUTOFF, thr))
        logger.info("GNN mark cutoff = %.4f (from %s)", _cached, settings.GNN_RUN_DIR)
    except Exception as exc:  # noqa: BLE001 — run missing/unreadable → config default
        logger.warning("tuned threshold unavailable (%s); using %.3f", exc, fallback)
        _cached = max(MIN_CUTOFF, min(MAX_CUTOFF, fallback))
    return _cached


def is_marked(gnn_score: Optional[float], in_cycle: bool, cutoff: float) -> bool:
    """The single definition of "our pipeline marked this account": sitting on
    a detected cycle, or GNN risk clearing the cutoff. Mirrored client-side by
    viz-style.js's isMarked() for instant recolouring on slider drag — a browser
    can't import this module, so that copy has to be kept in sync by hand."""
    return bool(in_cycle) or float(gnn_score or 0.0) >= cutoff


def marked_mask(scores: np.ndarray, in_cycle: np.ndarray, cutoff: float) -> np.ndarray:
    """Vectorised form of is_marked() for whole-graph score arrays."""
    return (scores >= float(cutoff)) | in_cycle
