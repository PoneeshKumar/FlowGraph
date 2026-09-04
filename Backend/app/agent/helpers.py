from typing import Optional

from Backend.app.schemas.graph import GraphElements
from app.services.graph_service import GraphService
from app.services.risk_aggregator import RiskAggregator, RiskVerdict

async def summarize_graph(account_id: str, depth: int = 2, limit: int = 100) -> GraphElements:
    return await GraphService.get_subgraph(account_id, depth, limit)

async def evaluate_risk(account_id: str, gnn_score: float, has_cycle: bool = False, cycle_length: Optional[int] = None) -> RiskVerdict:
    return await RiskAggregator.evaluate_account(
        account_id=account_id,
        gnn_score=gnn_score,
        has_cycle=has_cycle,
        cycle_length=cycle_length
    )

async def summarize_risky_applications(risk_tier: str = "high", limit: int = 100) -> dict:
    return await GraphService.get_risky_accounts(risk_tier=risk_tier, limit=limit)

