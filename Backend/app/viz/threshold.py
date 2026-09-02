"""The GNN mark cutoff — the model's own validation-tuned threshold.

The champion checkpoint stores the threshold that maximised validation F1
(``result.json['threshold']``, ≈0.74 for v10_L3). Marking at a hardcoded 0.5
instead — far below that operating point — is what floods the results with false
positives (whole graph: 7,074 FP / precision 0.27 at 0.5, vs 474 FP / precision
0.79 at 0.738). This loads the stored value once, falling back to the config
default if the run isn't present.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("viz.threshold")

# Slider bounds for the viewer — a sensible precision/recall exploration range.
MIN_CUTOFF = 0.50
MAX_CUTOFF = 0.95

_cached: Optional[float] = None


def model_threshold() -> float:
    """The model's tuned mark cutoff, cached. Falls back to MARK_GNN_THRESHOLD."""
    global _cached
    if _cached is not None:
        return _cached
    fallback = float(getattr(settings, "MARK_GNN_THRESHOLD", 0.5))
    try:
        result = Path(settings.GNN_RUN_DIR) / "result.json"
        thr = float(json.loads(result.read_text())["threshold"])
        # keep it inside the slider range so the default is always reachable
        _cached = max(MIN_CUTOFF, min(MAX_CUTOFF, thr))
        logger.info("GNN mark cutoff = %.4f (from %s)", _cached, result)
    except Exception as exc:  # noqa: BLE001 — run missing/unreadable → config default
        logger.warning("tuned threshold unavailable (%s); using %.3f", exc, fallback)
        _cached = fallback
    return _cached
