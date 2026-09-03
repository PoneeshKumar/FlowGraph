"""Live per-event GNN scoring.

Assembles a bounded neighborhood into the 47-column FeatureSet and scores it with
the resident LiveScorer, then writes the scores back — the incremental counterpart
to the whole-graph batch pass in the /viz PipelineRunner.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from ml.features import FeatureBuilder, FeatureSet

_WINDOWS = (1, 24, 168)


def assemble_neighborhood_features(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    volumes: Optional[Dict[str, Dict[str, float]]] = None,
    reference_time: Optional[datetime] = None,
    windows_hours: Sequence[int] = _WINDOWS,
) -> FeatureSet:
    """Build a FeatureSet from an exported neighborhood via the pure
    ``FeatureBuilder._assemble`` path (no store I/O).

    ``nodes``/``edges`` must be in ``export_neighborhood`` shape (== the batch
    ``export_account_nodes`` / ``export_flows_to_edges`` shape). ``volumes`` is the
    per-account Redis volume map; when omitted, the 12 volume features fall to 0.
    Column order matches the trained model contract, so LiveScorer accepts it.
    """
    ref = reference_time or datetime.now(timezone.utc)
    return FeatureBuilder(None)._assemble(
        nodes, edges, volumes or {}, [], windows_hours, ref, True
    )
