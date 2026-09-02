"""FastAPI routes for the community/pipeline visualiser, mounted under /viz."""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from app.db.neo4j import neo4j_client          # app session, for graph reads
from app.core.config import settings
from app.viz import store, deps
from app.viz.runner import PipelineRunner

router = APIRouter()
_STATIC = Path(__file__).parent / "static"


def _session():
    """Zero-arg factory the store calls to open a read session."""
    return neo4j_client.driver.session


@router.get("/")
async def index():
    idx = _STATIC / "index.html"
    if not idx.exists():
        raise HTTPException(status_code=404, detail="viewer page not built yet")
    return FileResponse(idx)


@router.get("/communities")
async def communities(sort: str = "risk", limit: int = Query(100, le=200), offset: int = 0):
    return await store.list_communities(_session(), sort, limit, offset)


@router.get("/overview")
async def overview(metric: str = "pagerank", limit: int = Query(600, ge=10, le=2000)):
    """Whole-graph map — induced subgraph on the top-``limit`` accounts by metric."""
    return await store.load_overview(_session(), metric=metric, limit=limit)


@router.get("/subgraph")
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


@router.get("/marked")
async def marked(sort: str = "score", signal: Optional[str] = None,
                 limit: int = Query(100, le=500), offset: int = 0):
    return await store.list_marked(deps.pg(), sort, signal, limit, offset)


@router.post("/run")
async def run(background: BackgroundTasks):
    if await deps.pg().get_active_pipeline_run():
        raise HTTPException(status_code=409, detail="a pipeline run is already active")
    run_id = await deps.pg().create_pipeline_run()
    runner = PipelineRunner(deps.neo4j(), deps.pg(), settings)
    background.add_task(runner.run, run_id)
    return {"run_id": run_id}


@router.get("/run/latest")
async def run_latest():
    return await deps.pg().get_latest_pipeline_run() or {"status": "none"}


@router.get("/run/{run_id}")
async def run_status(run_id: str):
    row = await deps.pg().get_pipeline_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="no such run")
    return row
