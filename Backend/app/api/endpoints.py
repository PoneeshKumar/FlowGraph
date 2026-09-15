# backend/app/api/endpoints.py
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import PlainTextResponse
from app.core.config import settings
from app.services import stats_service, alerts_service, transactions_service, explanation_service
from app.services import dataset_validation
from app.services.dataset_runner import DatasetRunner
from app.services.graph_service import GraphService
from app.schemas.api import (
    AlertPage, AlertOut, AlertStatusUpdate, TransactionPage, ExplanationOut, LLMStatus, UploadAccepted,
)
from app.db.neo4j import neo4j_client
from app.viz import deps as viz_deps
from app.schemas.graph import GraphElements, FlowSummaryResponse, NodeData
from app.services.risk_aggregator import RiskAggregator, RiskVerdict
router = APIRouter()

@router.get("/graph/subgraph", response_model=GraphElements)
async def get_subgraph(
    account_id: str = Query(..., description="Target node hash"),
    depth: int = Query(2, ge=1, le=4),
    limit: int = Query(100, ge=10, le=500)
):
    return await GraphService.get_subgraph(account_id, depth, limit)

@router.get("/graph/shortest-path", response_model=GraphElements)
async def get_shortest_path(account_a: str = Query(...), account_b: str = Query(...)):
    return await GraphService.get_shortest_path(account_a, account_b)

@router.get("/graph/account/{account_id}", response_model=NodeData)
async def get_account(account_id: str):
    node = await GraphService.get_account(account_id)
    if node is None:
        raise HTTPException(status_code=404, detail="no such account")
    return node

@router.get("/graph/flow", response_model=FlowSummaryResponse)
async def get_flow(
    account_a: str = Query(...),
    account_b: str = Query(...),
    window: str = Query("7d", pattern="^(1h|24h|7d|30d)$")
):
    flow_data = await GraphService.get_flow_between(account_a, account_b, window)
    return FlowSummaryResponse(**flow_data)

@router.get("/accounts/{account_id}/enrich", response_model=ExplanationOut)
async def enrich_account(account_id: str):
    return await explanation_service.service.explain(account_id)


@router.get("/system/llm", response_model=LLMStatus)
async def llm_status():
    return explanation_service.service.status()

@router.post("/risk/evaluate/{account_id}", response_model=RiskVerdict)
async def trigger_risk_evaluation(
    account_id: str,
    gnn_score: float = 0.52,
    has_cycle: bool = False,
    cycle_length: Optional[int] = None
):
    return await RiskAggregator.evaluate_account(
        account_id=account_id,
        gnn_score=gnn_score,
        has_cycle=has_cycle,
        cycle_length=cycle_length
    )


@router.get("/stats/overview")
async def stats_overview():
    if not stats_service.cache.ready():
        raise HTTPException(status_code=503, detail="stats warming up")
    return stats_service.cache.overview()


@router.get("/stats/volume-series")
async def stats_volume_series(
    currency: str = Query(..., description="Currency code, e.g. 'US Dollar'"),
    period: str = Query("7d", pattern="^(24h|7d|all)$"),
):
    if not stats_service.cache.ready():
        raise HTTPException(status_code=503, detail="stats warming up")
    try:
        return stats_service.cache.series(currency, period)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown currency: {currency}")


@router.get("/alerts", response_model=AlertPage)
async def list_alerts(
    flag_type: Optional[str] = Query(None, pattern="^[A-Z_]{2,20}$"),
    min_level: str = Query("low", pattern="^(low|medium|high|critical)$"),
    status: Optional[str] = Query("open", pattern="^(open|reviewed|dismissed|escalated)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await alerts_service.list_alerts(viz_deps.pg(), flag_type, min_level, status, limit, offset)


@router.patch("/alerts/{flag_id}/status", response_model=AlertOut)
async def update_alert_status(flag_id: int, body: AlertStatusUpdate):
    row = await alerts_service.set_status(viz_deps.pg(), flag_id, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="no such alert")
    return row


def _rail_for(currency: Optional[str]) -> Optional[str]:
    if not currency:
        return None
    from app.services.stats_service import cache
    return (cache.rail_for(currency) if cache.ready() else None) or currency


@router.get("/transactions", response_model=TransactionPage)
async def list_transactions(
    limit: int = Query(50, ge=1, le=200),
    account_id: Optional[str] = Query(None, min_length=1, max_length=128),
    currency: Optional[str] = Query(None, max_length=64),
):
    rail = _rail_for(currency)
    session = neo4j_client.driver.session
    if account_id:
        items = await transactions_service.list_for_account(session, account_id, limit=limit, rail=rail)
    else:
        items = await transactions_service.list_latest(session, limit=limit, rail=rail)
    return {"items": items}


# ---------------------------------------------------------------- datasets --

@router.get("/datasets/current")
async def dataset_current():
    row = await viz_deps.pg().get_app_meta("dataset")
    if row is None:
        raise HTTPException(status_code=404, detail="no dataset recorded")
    return row


@router.get("/datasets/template")
async def dataset_template():
    return PlainTextResponse(dataset_validation.template_csv(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="flowgraph-template.csv"'})


def _save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> int:
    """Stream to disk with a size cap; returns bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"file exceeds {max_bytes // (1024 * 1024)} MB")
            out.write(chunk)
    return written


@router.post("/datasets/upload", response_model=UploadAccepted)
async def dataset_upload(
    background: BackgroundTasks,
    transactions: UploadFile = File(...),
    patterns: Optional[UploadFile] = File(None),
    confirm: str = Form(""),
):
    """Replace the loaded graph with an uploaded transaction CSV and run the
    pipeline on it. Validation happens before anything is touched; the wipe only
    starts inside the background job."""
    if confirm != "replace":
        raise HTTPException(status_code=422, detail='confirm must be "replace" — this wipes the current graph')
    pg = viz_deps.pg()
    if await pg.get_active_pipeline_run():
        raise HTTPException(status_code=409, detail="a pipeline run is already active")
    max_bytes = int(settings.UPLOAD_MAX_MB) * 1024 * 1024
    staging = Path(settings.UPLOAD_DIR) / "staging"
    csv_path = staging / (transactions.filename or "transactions.csv")
    _save_upload(transactions, csv_path, max_bytes)
    check = dataset_validation.validate_transactions_csv(csv_path)
    if not check["ok"]:
        csv_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=check["error"])
    patterns_path: Optional[Path] = None
    if patterns is not None and patterns.filename:
        patterns_path = staging / patterns.filename
        _save_upload(patterns, patterns_path, max_bytes)

    run_id = await pg.create_pipeline_run()
    run_dir = Path(settings.UPLOAD_DIR) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    final_csv = run_dir / csv_path.name
    shutil.move(str(csv_path), final_csv)
    final_patterns: Optional[Path] = None
    if patterns_path is not None:
        final_patterns = run_dir / patterns_path.name
        shutil.move(str(patterns_path), final_patterns)

    runner = DatasetRunner(viz_deps.neo4j(), viz_deps.redis(), pg, settings)
    background.add_task(runner.run, run_id, final_csv, final_patterns, transactions.filename or "upload.csv")
    return {"run_id": run_id}
