# backend/app/services/ai_enrichment.py
"""Compatibility shim: the risk aggregator delegates low-confidence cases here.
The real work lives in app.services.explanation_service."""
from app.schemas.api import ExplanationOut


class AIEnrichmentService:
    @staticmethod
    async def generate_explanation(account_id: str) -> ExplanationOut:
        from app.services import explanation_service
        return await explanation_service.service.explain(account_id)
