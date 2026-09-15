"""PipelineRunner — orchestrates the existing detection stages for the visualiser.

Runs PageRank → Louvain → cycle → GNN inference → aggregate over the ingested
graph, writing progress to pipeline_runs as it goes. Inference only: it never
re-ingests transactions or retrains the GNN. Each stage is isolated so a failure
records which stage failed and stops.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import NEO4J_DATABASE
from fraud.community_detector import CommunityDetector
from fraud.cycle_detector import CycleDetector
from ml.ensemble import ensemble_scores
from ml.features import FeatureBuilder
from ml.train import load_feature_cache, save_feature_cache
from ml.predict import risk_level
from app.viz.aggregate import aggregate_account, MarkWeights, MarkThresholds

logger = logging.getLogger("viz.runner")

STAGES = ["pagerank", "louvain", "cycle", "gnn", "aggregate"]


class PipelineRunner:
    def __init__(self, neo4j, postgres, settings, redis=None, feature_source: str = "cache"):
        self.neo4j = neo4j
        self.pg = postgres
        self.redis = redis
        self.s = settings
        self.feature_source = feature_source   # "cache" (IBM .npz) | "live" (rebuild from stores)
        self._stage = None

    async def run(self, run_id: str) -> None:
        try:
            await self._pagerank(run_id)
            await self._louvain(run_id)
            cycle_members = await self._cycle(run_id)
            gnn_scores = await self._gnn(run_id)
            counts = await self._aggregate(run_id, gnn_scores, cycle_members)
            await self.pg.update_pipeline_run(
                run_id, status="completed", stage="aggregate",
                progress=1.0, counts=counts, finished=True)
        except Exception as exc:  # noqa: BLE001 — record any stage failure
            logger.exception("pipeline run %s failed", run_id)
            await self.pg.update_pipeline_run(
                run_id, status="failed", stage=self._stage,
                error=str(exc), finished=True)

    async def _mark(self, run_id, stage, progress):
        self._stage = stage
        await self.pg.update_pipeline_run(
            run_id, status="running", stage=stage, progress=progress)

    async def _pagerank(self, run_id):
        await self._mark(run_id, "pagerank", 0.1)
        await self.neo4j.recompute_pagerank_full()

    async def _louvain(self, run_id):
        await self._mark(run_id, "louvain", 0.3)
        await CommunityDetector(self.neo4j, self.pg).run()

    async def _cycle(self, run_id):
        await self._mark(run_id, "cycle", 0.5)
        ref, window_hours = await self._cycle_window()
        seeds = await self._cycle_seeds()
        detector = CycleDetector(self.neo4j, self.pg)
        members = set()
        for account_id in seeds:
            for flag in await detector.detect(
                    account_id, reference_time=ref, window_hours=window_hours):
                members.update(flag.get("account_ids", []))
        await self.neo4j.write_cycle_membership(members)
        return members

    async def _cycle_window(self):
        # The look-back window is anchored to the DATA, not wall-clock now. This is a
        # batch sweep over a historical dataset (the IBM AML graph spans years), so
        # find_cycles' default "last 48h from now" filter excludes every edge and
        # finds nothing. We anchor the reference time just past the newest edge and
        # widen the window to span the full history (CLAUDE.md: batch/investigative
        # sweeps use a wider window). Returns (reference_time, window_hours); (None,
        # None) when there are no timestamped edges, so the detector keeps its default.
        query = ("MATCH (:Account)-[f:FLOWS_TO]->(:Account) "
                 "RETURN min(f.last_ts) AS mn, max(f.last_ts) AS mx")
        async with self.neo4j.driver.session(database=NEO4J_DATABASE) as session:
            rec = await (await session.run(query)).single()
        if not rec or rec["mn"] is None or rec["mx"] is None:
            return None, None
        mn, mx = int(rec["mn"]), int(rec["mx"])
        ref = datetime.fromtimestamp(mx + 3600, tz=timezone.utc)   # just past newest edge
        window_hours = (mx - mn) // 3600 + 48                       # full span + margin
        return ref, window_hours

    async def _cycle_seeds(self):
        # CycleDetector.detect is per-account (a bounded DFS seeded from one node), so
        # a full 513k-account sweep is impractical on-demand — seed selection decides
        # what gets found. Members of a reciprocal pair (a→b→a) are guaranteed to sit
        # on a 2-cycle, so they are by far the highest-yield seeds; an arbitrary "has
        # outgoing flow" sample almost never lands on a ring. CYCLE_MAX_SEEDS bounds
        # the work. (The GNN already covers cycle typologies; this stage is
        # illustrative of the detector, not exhaustive.)
        query = ("MATCH (a:Account)-[:FLOWS_TO]->(b:Account)-[:FLOWS_TO]->(a) "
                 "WHERE a.id < b.id RETURN a.id AS id LIMIT $limit")
        async with self.neo4j.driver.session(database=NEO4J_DATABASE) as session:
            res = await session.run(query, limit=self.s.CYCLE_MAX_SEEDS)
            return [r["id"] async for r in res]

    async def _gnn(self, run_id):
        await self._mark(run_id, "gnn", 0.7)
        run_dir = Path(self.s.GNN_RUN_DIR)
        if not run_dir.exists():
            logger.warning("GNN checkpoint missing (%s) — skipping GNN stage; "
                           "marks fall back to cycle + community signals", run_dir)
            return {}
        if self.feature_source == "live":
            feature_set = await self._build_live_features()
            upload_cache = Path(self.s.GNN_FEATURE_CACHE_UPLOAD)
            save_feature_cache(feature_set, upload_cache)
            self.s.GNN_FEATURE_CACHE = str(upload_cache)   # later plain runs reuse it
        else:
            cache = Path(self.s.GNN_FEATURE_CACHE)
            if not cache.exists():
                logger.warning("GNN feature cache missing (%s) — skipping GNN stage; "
                               "marks fall back to cycle + community signals", cache)
                return {}
            feature_set = load_feature_cache(cache)
        # Ensemble members are optional artifacts (ml/runs is gitignored); skip any
        # that aren't on disk so serving falls back to the single champion instead
        # of crashing. ensemble_scores of one member == that member's scores.
        members = [p for p in self.s.GNN_ENSEMBLE_RUNS if Path(p).exists()]
        run_dirs = [run_dir] + [Path(p) for p in members]
        logger.info("GNN scoring with %d model(s): %s",
                    len(run_dirs), ", ".join(d.name for d in run_dirs))
        scores = ensemble_scores(run_dirs, feature_set)
        mapping = {nid: float(sc) for nid, sc in zip(feature_set.node_ids, scores)}
        await self.neo4j.write_gnn_scores(mapping, tier_of=risk_level)
        return mapping

    async def _build_live_features(self):
        """FeatureSet from the stores — for an uploaded graph the IBM .npz is wrong.
        Anchored at the 99.9th-percentile edge timestamp so a historical file isn't
        filtered out as stale (same rule as ml/train.build_feature_set)."""
        anchor = await self.neo4j.get_flows_to_timestamp(percentile=0.999)
        reference = datetime.fromtimestamp(anchor, tz=timezone.utc) if anchor else None
        builder = FeatureBuilder(self.neo4j, self.redis, self.pg)
        return await builder.build(window_days=3650, reference_time=reference, export_timeout_seconds=3600.0)

    async def _aggregate(self, run_id, gnn_scores, cycle_members):
        await self._mark(run_id, "aggregate", 0.9)
        from app.viz import threshold, metrics
        metrics.invalidate()                     # scores just changed — drop stale cache
        threshold.invalidate()                    # pick up a hot-swapped run's tuned cutoff
        from app.services import stats_service
        stats_service.invalidate()               # open-flag counts and latest_run changed
        weights = MarkWeights()
        thresholds = MarkThresholds(gnn=threshold.model_threshold())
        marked = 0
        # v1 boundary: community-tier signal is joined once persisted per-account;
        # marks here are driven by the GNN + cycle signals, which are correct.
        for aid in set(gnn_scores) | set(cycle_members):
            rec = aggregate_account(
                aid, gnn_scores.get(aid), aid in cycle_members, None,
                weights, thresholds)
            if rec is None:
                continue
            marked += 1
            await self.pg.upsert_risk_flag(
                flag_type="AGGREGATE", fingerprint=f"agg:{aid}", account_ids=[aid],
                risk_level=risk_level(rec["combined_score"]),
                risk_score=rec["combined_score"], explanation=rec["rationale"],
                details=rec)
        return {"cycles": len(cycle_members), "gnn_scored": len(gnn_scores), "marked": marked}
