# backend/app/api/endpoints.py
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from app.services import stats_service, alerts_service, transactions_service, explanation_service
from app.services.graph_service import GraphService
from app.schemas.api import (
    AlertPage, AlertOut, AlertStatusUpdate, TransactionPage, ExplanationOut, LLMStatus,
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
