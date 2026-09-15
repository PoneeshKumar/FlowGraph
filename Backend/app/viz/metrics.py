"""Whole-graph confusion metrics at an arbitrary GNN cutoff.

The viewer's cutoff slider needs true whole-graph precision/recall at any
threshold, live. Re-querying 514k rows per drag is far too slow, so the scores,
cycle flags and ground-truth labels are loaded once into numpy arrays and every
cutoff is then an O(n) vectorised pass. ``invalidate()`` drops the cache after a
pipeline run rewrites the scores.
"""
import asyncio
import logging
from typing import Any, Dict, Optional  # noqa: F401 — Any is used by _lock_loop

import numpy as np

from app.viz import threshold, truth
from ml.evaluate import fraud_metrics

logger = logging.getLogger("viz.metrics")

_scores: Optional[np.ndarray] = None
_in_cycle: Optional[np.ndarray] = None
_labels: Optional[np.ndarray] = None
_load_lock: Optional[asyncio.Lock] = None
_lock_loop: Optional[Any] = None


def _lock() -> asyncio.Lock:
    """The load lock, bound to the *running* loop.

    A module-level asyncio.Lock() binds to whichever loop was current at import
    time (Python 3.9), so awaiting it from another loop raises "got Future
    attached to a different loop" — which happens as soon as both the app's
    startup warm-up and a request can trigger a load. Rebuild it when the loop
    changes; within one loop it is the same lock, so the race is still covered.
    """
    global _load_lock, _lock_loop
    loop = asyncio.get_event_loop()
    if _load_lock is None or _lock_loop is not loop:
        _load_lock = asyncio.Lock()
        _lock_loop = loop
    return _load_lock


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
    async with _lock():
        if _scores is not None:   # someone else won the race while we waited
            return
        query = ("MATCH (a:Account) WHERE a.gnn_risk_score IS NOT NULL OR a.in_cycle "
                 "RETURN a.id AS id, coalesce(a.gnn_risk_score, 0.0) AS sc, coalesce(a.in_cycle, false) AS ic")
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
    use (``threshold.is_marked`` / ``.marked_mask``). Returns counts plus
    precision/recall (0.0 when undefined)."""
    if _scores is None:
        return {"loaded": False}
    pred = threshold.marked_mask(_scores, _in_cycle, cutoff)
    m = fraud_metrics(_labels, pred)
    total = int(_labels.size)
    tn = total - m.true_positives - m.false_positives - m.false_negatives
    return {"loaded": True, "cutoff": float(cutoff), "marked": m.predicted_positive,
            "tp": m.true_positives, "fp": m.false_positives, "fn": m.false_negatives, "tn": tn,
            "precision": m.precision, "recall": m.recall, "total": total}
