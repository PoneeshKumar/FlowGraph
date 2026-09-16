"""Explanation service: evidence, prompt, rule-based fallback, provider selection,
fallback on provider failure, and the two routes. No network anywhere."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.schemas.api import ModelJSON
from app.services import explanation_service as es


def _node(**over):
    base = {"id": "acct1", "gnn_risk_score": 0.83, "gnn_risk_tier": "high", "in_cycle": False,
            "community_id": "c9", "pagerank_score": 1e-5}
    base.update(over)
    return base


def test_build_evidence_and_prompt():
    ev = es.build_evidence(
        _node(), payers=[{"id": "p1", "amount": 5000.0, "n": 3, "tier": "low"}],
        payees=[{"id": "q1", "amount": 100.0, "n": 1, "tier": "critical"}],
        flags=[{"flag_type": "AGGREGATE", "risk_level": "high", "explanation": "Marked (GNN 0.83)."}],
        dataset={"name": "T", "labelled": True})
    assert ev.account_id == "acct1" and ev.gnn_risk_score == 0.83 and ev.payees[0]["tier"] == "critical"
    p = es.evidence_prompt(ev)
    assert "acct1" in p and "0.83" in p and "AGGREGATE" in p and "q1" in p
    assert "typology" not in p.lower().split("evidence")[0]   # no label hints in the header


def test_rule_based_tiers_and_typology_hint():
    hi = es.rule_based(es.build_evidence(_node(gnn_risk_score=0.9, in_cycle=True), [], [], [], {}))
    assert hi.risk_level == "critical" and hi.detected_typology == "CYCLE" and hi.confidence >= 0.9
    lo = es.rule_based(es.build_evidence(_node(gnn_risk_score=0.1, gnn_risk_tier=None), [], [], [], {}))
    assert lo.risk_level == "low" and lo.detected_typology is None
    fan = es.rule_based(es.build_evidence(
        _node(gnn_risk_score=0.7, gnn_risk_tier="high"), [], [{"id": str(i), "amount": 1.0, "n": 1, "tier": "low"} for i in range(6)], [], {}))
    assert fan.detected_typology == "FAN-OUT"


def test_resolve_provider_precedence():
    assert es.resolve_provider("ollama", "sk", True) == "ollama"        # explicit wins
    assert es.resolve_provider("", "sk", True) == "anthropic"           # key beats reachable ollama
    assert es.resolve_provider("", "", True) == "ollama"
    assert es.resolve_provider("", "", False) == "none"
    assert es.resolve_provider("bogus", "", False) == "none"


class _Provider:
    name, model = "fake", "fake-1"
    def __init__(self, result=None, exc=None): self.result, self.exc = result, exc
    async def generate(self, ev):
        if self.exc: raise self.exc
        return self.result


def _service(provider):
    svc = es.ExplanationService(lambda: None, lambda: None, provider=provider)
    ev = es.build_evidence(_node(), [], [], [], {"name": "T"})
    svc.gather = AsyncMock(return_value=ev)
    return svc


def test_explain_uses_provider_and_adds_provenance():
    good = ModelJSON(risk_level="high", confidence=0.7, explanation="fan-in hub", detected_typology="FAN-IN",
                     compliance_summary="review")
    out = asyncio.run(_service(_Provider(result=good)).explain("acct1"))
    assert out.provider == "fake" and out.model == "fake-1" and out.explanation == "fan-in hub"
    assert out.account_id == "acct1" and out.generated_at > 0


def test_explain_falls_back_when_provider_fails():
    out = asyncio.run(_service(_Provider(exc=RuntimeError("boom"))).explain("acct1"))
    assert out.provider == "none" and out.risk_level == "high" and out.explanation


def test_explain_unknown_account_is_rule_based_low():
    svc = es.ExplanationService(lambda: None, lambda: None, provider=_Provider())
    svc.gather = AsyncMock(return_value=None)
    out = asyncio.run(svc.explain("nope"))
    assert out.provider == "none" and out.risk_level == "low"


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()), \
         patch.object(es, "configure", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_risk_aggregator_delegates_low_confidence_to_service():
    """gnn_score inside ±0.15 of the 0.45 threshold → the aggregator asks the
    explanation service and returns its verdict (this path used to 500 twice:
    a dict attribute access, then wrong keyword names into _finalize_verdict)."""
    from app.services.risk_aggregator import RiskAggregator

    class _Sess:
        async def run(self, q, **p): return None
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False

    good = ModelJSON(risk_level="high", confidence=0.66, explanation="borderline hub",
                     detected_typology="FAN-IN", compliance_summary="c")
    with patch.object(es, "service", _service(_Provider(result=good))), \
         patch("app.services.risk_aggregator.neo4j_client") as nc:
        nc.driver.session = lambda: _Sess()
        v = asyncio.run(RiskAggregator.evaluate_account("acct1", gnn_score=0.45))
    assert v.delegated_to_ai is True and v.risk_tier == "high" and v.confidence == 0.66
    assert "borderline hub" in v.explanation and "FAN-IN" in v.explanation


def test_enrich_and_llm_status_routes(client):
    good = ModelJSON(risk_level="medium", confidence=0.5, explanation="e", compliance_summary="c")
    svc = _service(_Provider(result=good))
    with patch.object(es, "service", svc):
        r = client.get("/api/accounts/acct1/enrich")
        assert r.status_code == 200 and r.json()["provider"] == "fake" and r.json()["risk_level"] == "medium"
        s = client.get("/api/system/llm")
        assert s.status_code == 200 and s.json() == {"provider": "fake", "model": "fake-1", "reachable": True}
