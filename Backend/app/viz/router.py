"""FastAPI routes for the community/pipeline visualiser, mounted under /viz."""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from app.db.neo4j import neo4j_client          # app session, for graph reads
from app.core.config import settings
from app.viz import store, deps, metrics, threshold
from app.viz.runner import PipelineRunner

pages = APIRouter()   # the server-rendered viewer page — mounted at /viz only
api = APIRouter()     # JSON routes — mounted at /viz and again at /api/pipeline
router = APIRouter()  # combined; existing imports and tests use this name
_STATIC = Path(__file__).parent / "static"


def _session():
    """Zero-arg factory the store calls to open a read session."""
    return neo4j_client.driver.session


@pages.get("/")
async def index():
    idx = _STATIC / "index.html"
    if not idx.exists():
        raise HTTPException(status_code=404, detail="viewer page not built yet")
    return FileResponse(idx)


@api.get("/communities")
async def communities(sort: str = "risk", limit: int = Query(100, le=200), offset: int = 0):
    return await store.list_communities(_session(), sort, limit, offset)


@api.get("/overview")
async def overview(metric: str = "pagerank", limit: int = Query(600, ge=10, le=2000)):
    """Whole-graph map — induced subgraph on the top-``limit`` accounts by metric."""
    return await store.load_overview(_session(), metric=metric, limit=limit)


@api.get("/subgraph")
async def subgraph(
    community_id: Optional[str] = None,
    account_id: Optional[str] = None,
    hops: int = Query(2, ge=1, le=4),
    limit: int = Query(150, le=500),
):
    if not community_id and not account_id:
        raise HTTPException(status_code=422, detail="provide community_id or account_id")
    return await store.load_subgraph(
        _session(), community_id=community_id, account_id=account_id,
        hops=hops, limit=limit)


@api.get("/marked")
async def marked(sort: str = "score", signal: Optional[str] = None,
                 limit: int = Query(100, le=500), offset: int = 0):
    return await store.list_marked(deps.pg(), sort, signal, limit, offset)


@api.get("/threshold")
async def threshold_config():
    """The model's tuned mark cutoff and the slider's bounds."""
    return {"default": threshold.model_threshold(),
            "min": threshold.MIN_CUTOFF, "max": threshold.MAX_CUTOFF}


@api.get("/metrics")
async def metrics_at(cutoff: float = Query(..., ge=0.0, le=1.0)):
    """Whole-graph precision/recall/confusion at a given GNN cutoff."""
    await metrics.ensure_loaded(_session())
    return metrics.confusion_at(cutoff)


@api.post("/run")
async def run(background: BackgroundTasks):
    if await deps.pg().get_active_pipeline_run():
        raise HTTPException(status_code=409, detail="a pipeline run is already active")
    run_id = await deps.pg().create_pipeline_run()
    runner = PipelineRunner(deps.neo4j(), deps.pg(), settings)
    background.add_task(runner.run, run_id)
    return {"run_id": run_id}


@api.get("/run/latest")
async def run_latest():
    return await deps.pg().get_latest_pipeline_run() or {"status": "none"}


@api.get("/run/{run_id}")
async def run_status(run_id: str):
    row = await deps.pg().get_pipeline_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="no such run")
    return row


@api.get("/metrics/curve")
async def metrics_curve(points: int = Query(46, ge=2, le=200)):
    """Precision/recall/counts at evenly spaced cutoffs — lets a client draw the
    curve and interpolate slider positions without a request per drag (and lets
    the static demo bake it)."""
    await metrics.ensure_loaded(_session())
    lo, hi = threshold.MIN_CUTOFF, threshold.MAX_CUTOFF
    cutoffs = [round(lo + (hi - lo) * i / (points - 1), 4) for i in range(points)]
    rows = [metrics.confusion_at(c) for c in cutoffs]
    if not rows or not rows[0].get("loaded"):
        return {"loaded": False}
    return {
        "loaded": True,
        "cutoffs": cutoffs,
        "precision": [r["precision"] for r in rows],
        "recall": [r["recall"] for r in rows],
        "tp": [r["tp"] for r in rows],
        "fp": [r["fp"] for r in rows],
        "marked": [r["marked"] for r in rows],
    }


router.include_router(pages)
router.include_router(api)
