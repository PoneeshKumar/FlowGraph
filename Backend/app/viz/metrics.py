"""Whole-graph confusion metrics at an arbitrary GNN cutoff.

The viewer's cutoff slider needs true whole-graph precision/recall at any
threshold, live. Re-querying 514k rows per drag is far too slow, so the scores,
cycle flags and ground-truth labels are loaded once into numpy arrays and every
cutoff is then an O(n) vectorised pass. ``invalidate()`` drops the cache after a
pipeline run rewrites the scores.
"""
import logging
from typing import Any, Dict, Optional

import numpy as np

from app.viz import truth

logger = logging.getLogger("viz.metrics")

_scores: Optional[np.ndarray] = None
_in_cycle: Optional[np.ndarray] = None
_labels: Optional[np.ndarray] = None


def invalidate() -> None:
    global _scores, _in_cycle, _labels
    _scores = _in_cycle = _labels = None


def loaded() -> bool:
    return _scores is not None


async def ensure_loaded(session) -> None:
    """Load (score, in_cycle, truth-label) arrays from Neo4j once. ``session`` is a
    zero-arg factory returning an async session context (as in ``store``)."""
    global _scores, _in_cycle, _labels
    if _scores is not None:
        return
    query = ("MATCH (a:Account) WHERE a.gnn_risk_score IS NOT NULL "
             "RETURN a.id AS id, a.gnn_risk_score AS sc, coalesce(a.in_cycle, false) AS ic")
    ids, sc, ic = [], [], []
    async with session() as s:
        res = await s.run(query)
        async for r in res:
            ids.append(r["id"]); sc.append(float(r["sc"])); ic.append(bool(r["ic"]))
    tset = truth.truth_set()
    _scores = np.asarray(sc, dtype=np.float64)
    _in_cycle = np.asarray(ic, dtype=bool)
    _labels = np.fromiter((i in tset for i in ids), dtype=bool, count=len(ids))
    logger.info("metrics cache: %d scored accounts, %d labelled", len(ids), int(_labels.sum()))


def confusion_at(cutoff: float) -> Dict[str, Any]:
    """Whole-graph confusion at ``cutoff``. An account is marked when its GNN score
    clears the cutoff OR it sits on a detected cycle — the same rule the graph tabs
    use. Returns counts plus precision/recall (0.0 when undefined)."""
    if _scores is None:
        return {"loaded": False}
    pred = (_scores >= float(cutoff)) | _in_cycle
    y = _labels
    tp = int(np.sum(pred & y)); fp = int(np.sum(pred & ~y))
    fn = int(np.sum(~pred & y)); tn = int(np.sum(~pred & ~y))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {"loaded": True, "cutoff": float(cutoff), "marked": tp + fp,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "total": int(y.size)}
