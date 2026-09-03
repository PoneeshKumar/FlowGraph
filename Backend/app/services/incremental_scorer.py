"""Live per-event GNN scoring.

Assembles a bounded neighborhood into the 47-column FeatureSet and scores it with
the resident LiveScorer, then writes the scores back — the incremental counterpart
to the whole-graph batch pass in the /viz PipelineRunner.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ml.features import FeatureBuilder, FeatureSet

logger = logging.getLogger("incremental_scorer")

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


class IncrementalScorer:
    """Re-score the accounts a payment event touched, plus their bounded k-hop
    neighborhood, with the resident GNN — the live counterpart to the /viz batch
    pass. Reads a bounded neighborhood, assembles its features, scores it, and
    writes the scores back (``gnn_risk_score`` for the whole affected set, a
    ``LIVE_GNN`` risk flag for the seeds).

    v1 accuracy caveats (documented): PageRank/Louvain use last-batch stored props
    (not recomputed per event) and the 12 Redis volume features are zeroed
    (``get_all_account_volumes`` is a whole-keyspace scan, too costly per event) —
    what's fresh is the GNN message passing over the live graph structure. Live
    scores therefore differ slightly from batch scores; this is by design.
    """

    def __init__(self, neo4j, redis, postgres, live_scorer, *,
                 hops: int = 3, fanout: int = 10, max_affected: int = 300,
                 windows_hours: Sequence[int] = _WINDOWS):
        self.neo4j = neo4j
        self.redis = redis
        self.postgres = postgres
        self.scorer = live_scorer
        self.hops = hops
        self.fanout = fanout
        self.max_affected = max_affected
        self.windows_hours = windows_hours

    async def score_touched(self, touched: Iterable[str]) -> Dict[str, float]:
        """Re-score the affected neighborhood of ``touched`` and write scores back.
        Returns the ``{account_id: score}`` map of every rescored account."""
        seeds = [s for s in dict.fromkeys(touched) if s]   # dedupe, keep order, drop falsy
        if not seeds:
            return {}

        nodes, edges = await self.neo4j.export_neighborhood(
            seeds, self.hops, self.fanout, self.max_affected)
        if not nodes:
            return {}

        volumes = await self._volumes(nodes)               # v1: {} (see class docstring)
        feature_set = assemble_neighborhood_features(
            nodes, edges, volumes=volumes, windows_hours=self.windows_hours)
        scores = self.scorer.score(feature_set)
        mapping = {nid: float(s) for nid, s in zip(feature_set.node_ids, scores)}

        from ml.predict import risk_level
        await self.neo4j.write_gnn_scores(mapping, tier_of=risk_level)   # whole affected set
        for sid in seeds:                                  # a flag only for what the event touched
            sc = mapping.get(sid)
            if sc is None:
                continue
            await self.postgres.upsert_risk_flag(
                flag_type="LIVE_GNN", fingerprint=f"live_gnn:{sid}", account_ids=[sid],
                risk_level=risk_level(sc), risk_score=sc,
                explanation=(f"Live GNN re-score {sc:.3f} ({risk_level(sc)}) after a payment "
                             f"event; rescored {len(mapping)} accounts in the {self.hops}-hop "
                             f"neighborhood."),
                details={"gnn_score": sc, "affected": len(mapping), "source": "live"})

        logger.info("live-scored %d accounts from %d seed(s)", len(mapping), len(seeds))
        return mapping

    async def _volumes(self, nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
        """v1: no per-event volume fetch (whole-keyspace scan is too costly here).
        Volume features zero-fill; reconstructing them from the neighborhood's known
        edge ZSETs is the documented next step."""
        return {}
