from typing import Optional, Dict, Any
from pydantic import BaseModel
from app.db.neo4j import neo4j_client
from app.services.ai_enrichment import AIEnrichmentService

class RiskVerdict(BaseModel):
    account_id: str
    risk_score: float
    risk_tier: str 
    confidence: float
    triggering_signals: Dict[str, Any]
    explanation: str
    delegated_to_ai: bool = False

class RiskAggregator:
    F1_THRESHOLD = 0.45
    CONFIDENCE_MARGIN = 0.15 

    @classmethod
    async def evaluate_account(
        cls,
        account_id: str,
        gnn_score: float,
        has_cycle: bool = False,
        cycle_length: Optional[int] = None,
        pagerank_percentile: float = 0.0,
        community_risk_score: float = 0.0,
    ) -> RiskVerdict:
        signals = {
            "gnn_score": gnn_score,
            "has_cycle": has_cycle,
            "cycle_length": cycle_length,
            "pagerank_percentile": pagerank_percentile,
            "community_risk_score": community_risk_score,
        }

        blended_score = (gnn_score * 0.75) + (community_risk_score * 0.15) + (pagerank_percentile * 0.10)

        if has_cycle:
            blended_score = max(blended_score, 0.92)
            risk_tier = "critical" if (cycle_length and cycle_length <= 4) else "high"
            confidence = 0.95
            explanation = f"Deterministic circular flow confirmed ({cycle_length}-hop cycle). Immediate escalation."
            return await cls._finalize_verdict(account_id, blended_score, risk_tier, confidence, signals, explanation)

        distance = abs(gnn_score - cls.F1_THRESHOLD)
        base_confidence = min(1.0, 0.50 + (distance / cls.F1_THRESHOLD) * 0.50)

        is_low_confidence = (cls.F1_THRESHOLD - cls.CONFIDENCE_MARGIN) <= gnn_score <= (cls.F1_THRESHOLD + cls.CONFIDENCE_MARGIN)
        
        if is_low_confidence and base_confidence < 0.70:
            ai_report = await AIEnrichmentService.generate_explanation(account_id)
            return await cls._finalize_verdict(
                account_id=account_id,
                risk_score=blended_score,
                risk_tier=ai_report.risk_level,
                confidence=ai_report.confidence,
                signals=signals,
                explanation=f"[AI Enriched - {ai_report.detected_typology}] {ai_report.explanation}",
                delegated_to_ai=True
            )

        if blended_score >= 0.75:
            risk_tier = "critical"
            explanation = f"GNN identified strong topological laundering patterns (score: {blended_score:.2f})."
        elif blended_score >= 0.50:
            risk_tier = "high"
            explanation = f"Elevated structuring/hub activity detected above baseline (score: {blended_score:.2f})."
        elif blended_score >= 0.25:
            risk_tier = "medium"
            explanation = f"Moderate behavioral variance; within review parameters (score: {blended_score:.2f})."
        else:
            risk_tier = "low"
            explanation = f"Normal transaction velocity and standard fan-in/out distribution (score: {blended_score:.2f})."

        return await cls._finalize_verdict(account_id, blended_score, risk_tier, base_confidence, signals, explanation)

    @classmethod
    async def _finalize_verdict(
        cls, 
        account_id: str, 
        score: float, 
        tier: str, 
        confidence: float, 
        signals: dict, 
        explanation: str,
        delegated_to_ai: bool = False
    ) -> RiskVerdict:
        verdict = RiskVerdict(
            account_id=account_id,
            risk_score=round(score, 4),
            risk_tier=tier,
            confidence=round(confidence, 2),
            triggering_signals=signals,
            explanation=explanation,
            delegated_to_ai=delegated_to_ai
        )
        query = """
        MATCH (a:Account {id: $account_id})
        SET a.risk_score = $risk_score,
            a.risk_tier = $risk_tier,
            a.last_evaluated = timestamp()
        """
        async with neo4j_client.driver.session() as session:
            await session.run(query, account_id=account_id, risk_score=verdict.risk_score, risk_tier=verdict.risk_tier)

        return verdict