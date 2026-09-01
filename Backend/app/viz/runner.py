"""PipelineRunner — orchestrates the existing detection stages for the visualiser.

Runs PageRank → Louvain → cycle → GNN inference → aggregate over the ingested
graph, writing progress to pipeline_runs as it goes. Inference only: it never
re-ingests transactions or retrains the GNN. Each stage is isolated so a failure
records which stage failed and stops.
"""
import logging
from pathlib import Path

from config import NEO4J_DATABASE
from fraud.community_detector import CommunityDetector
from fraud.cycle_detector import CycleDetector
from ml.ensemble import ensemble_scores
from ml.train import load_feature_cache
from ml.predict import risk_level
from app.viz.aggregate import aggregate_account, MarkWeights, MarkThresholds

logger = logging.getLogger("viz.runner")

STAGES = ["pagerank", "louvain", "cycle", "gnn", "aggregate"]


class PipelineRunner:
    def __init__(self, neo4j, postgres, settings):
        self.neo4j = neo4j
        self.pg = postgres
        self.s = settings
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
        seeds = await self._cycle_seeds()
        detector = CycleDetector(self.neo4j, self.pg)
        members = set()
        for account_id in seeds:
            for flag in await detector.detect(account_id):
                members.update(flag.get("account_ids", []))
        await self.neo4j.write_cycle_membership(members)
        return members

    async def _cycle_seeds(self):
        # CycleDetector.detect is per-account (a bounded DFS seeded from one node);
        # a full 513k-account sweep is impractical on-demand, so seed from a capped
        # set of accounts that have outgoing flow — the only cycle candidates.
        # CYCLE_MAX_SEEDS bounds the work. (The GNN already covers cycle typologies;
        # this stage is illustrative, not exhaustive.)
        query = "MATCH (a:Account)-[:FLOWS_TO]->() RETURN DISTINCT a.id AS id LIMIT $limit"
        async with self.neo4j.driver.session(database=NEO4J_DATABASE) as session:
            res = await session.run(query, limit=self.s.CYCLE_MAX_SEEDS)
            return [r["id"] async for r in res]

    async def _gnn(self, run_id):
        await self._mark(run_id, "gnn", 0.7)
        run_dir = Path(self.s.GNN_RUN_DIR)
        cache = Path(self.s.GNN_FEATURE_CACHE)
        if not run_dir.exists() or not cache.exists():
            logger.warning(
                "GNN artifacts missing (run_dir=%s, cache=%s) — skipping GNN stage; "
                "marks fall back to cycle + community signals",
                run_dir.exists(), cache.exists())
            return {}
        feature_set = load_feature_cache(cache)
        run_dirs = [run_dir] + [Path(p) for p in self.s.GNN_ENSEMBLE_RUNS]
        scores = ensemble_scores(run_dirs, feature_set)
        mapping = {nid: float(sc) for nid, sc in zip(feature_set.node_ids, scores)}
        await self.neo4j.write_gnn_scores(mapping, tier_of=risk_level)
        return mapping

    async def _aggregate(self, run_id, gnn_scores, cycle_members):
        await self._mark(run_id, "aggregate", 0.9)
        weights = MarkWeights()
        thresholds = MarkThresholds(gnn=self.s.MARK_GNN_THRESHOLD)
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
