# backend/app/api/endpoints.py
from typing import Optional

from fastapi import APIRouter, Query
from app.services.graph_service import GraphService
from app.services.ai_enrichment import AIEnrichmentService
from app.schemas.graph import (
    GraphElements,
    FlowSummaryResponse,
    AIReportResponse,
    BusinessSummaryRequest,
    BusinessSummaryResponse,
)
from app.services.risk_aggregator import RiskAggregator, RiskVerdict
router = APIRouter()

def _fallback_business_summary(question: str, data: Dict[str, Any]) -> str:
    return (
        f"Business {data['business_id']} has {data['total_accounts']} accounts. "
        f"{data['risky_accounts']} meet the selected risk threshold, with an average "
        f"risk score of {data['average_risk_score']:.2f}. "
        f"The selected question was: {question}"
    )

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

@router.get("/graph/flow", response_model=FlowSummaryResponse)
async def get_flow(
    account_a: str = Query(...),
    account_b: str = Query(...),
    window: str = Query("7d", pattern="^(1h|24h|7d|30d)$")
):
    flow_data = await GraphService.get_flow_between(account_a, account_b, window)
    return FlowSummaryResponse(**flow_data)

@router.get("/accounts/{account_id}/enrich", response_model=AIReportResponse)
async def enrich_account(account_id: str):
    return await AIEnrichmentService.generate_explanation(account_id)

@router.post("/agent/business-summary", response_model=BusinessSummaryResponse)
async def summarize_business(request: BusinessSummaryRequest):
    from app.agent.helpers import summarize_business as get_business_data

    data = await get_business_data(request.business_id, request.risk_tier, request.limit)
    summary = _fallback_business_summary(request.question, data)
    generated_by = "structured-fallback"

    try:
        from app.agent.tools import agent

        prompt = (
            "Answer the user's question using only this business-scoped risk data. "
            "Do not invent transactions or accounts. Be concise and mention important "
            f"risk patterns. Business data: {data}. User question: {request.question}"
        )
        result = await agent.run(prompt)
        summary = getattr(result, "output", getattr(result, "data", summary))
        generated_by = "agent"
    except Exception:
        pass

    return BusinessSummaryResponse(
        business_id=request.business_id,
        question=request.question,
        summary=str(summary),
        data=data,
        generated_by=generated_by,
    )

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