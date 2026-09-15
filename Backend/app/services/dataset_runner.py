"""The "Analyze a file" job: replace the loaded graph with an uploaded CSV and run
the whole pipeline on it (spec §6.10). One row in pipeline_runs tracks it; stages
are reset → ingest → pagerank → louvain → cycle → gnn → aggregate.

Destructive by design: `reset` wipes Neo4j, Redis and risk_flags. The API only
launches this after the client confirmed "replace" and the file validated.
"""
import logging
from pathlib import Path
from typing import Any, Optional

from ml.datasets.ibm_aml import ingest_for_training, reset_stores
from app.services import stats_service
from app.viz import metrics, threshold, truth
from app.viz.runner import PipelineRunner

logger = logging.getLogger("dataset_runner")


def _ts(dt: Any) -> Optional[int]:
    return int(dt.timestamp()) if dt is not None else None


class DatasetRunner:
    def __init__(self, neo4j: Any, redis: Any, pg: Any, settings: Any) -> None:
        self.neo4j, self.redis, self.pg, self.s = neo4j, redis, pg, settings
        self._stage: Optional[str] = None

    async def _mark(self, run_id: str, stage: str, progress: float) -> None:
        self._stage = stage
        await self.pg.update_pipeline_run(run_id, status="running", stage=stage, progress=progress)

    async def run(self, run_id: str, csv_path: Path, patterns_path: Optional[Path], name: str) -> None:
        try:
            await self._mark(run_id, "reset", 0.02)
            await reset_stores(self.neo4j, self.redis)
            await self.pg.clear_risk_flags()

            await self._mark(run_id, "ingest", 0.05)
            stats = await ingest_for_training(
                csv_path, patterns_path, self.neo4j, self.redis,
                max_background_rows=None, recompute_pagerank=False)   # the pipeline's own stage does PageRank
            truth.reload(patterns_path)
            await self.pg.set_app_meta("dataset", {
                "name": name, "source": "upload", "labelled": patterns_path is not None,
                "start_ts": _ts(getattr(stats, "min_timestamp", None)),
                "end_ts": _ts(getattr(stats, "max_timestamp", None)),
                "rows": getattr(stats, "rows_scanned", None), "uploaded_at": None,
            })
            stats_service.invalidate()
            metrics.invalidate()
            threshold.invalidate()
            logger.info("dataset %s ingested (%s rows); running the pipeline", name, getattr(stats, "rows_scanned", "?"))
        except Exception as exc:  # noqa: BLE001 — record which stage failed
            logger.exception("dataset run %s failed at %s", run_id, self._stage)
            await self.pg.update_pipeline_run(run_id, status="failed", stage=self._stage, error=str(exc), finished=True)
            return

        runner = PipelineRunner(self.neo4j, self.pg, self.s, redis=self.redis, feature_source="live")
        await runner.run(run_id)          # marks pagerank … aggregate and completed/failed itself
