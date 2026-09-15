"""Graph service: GNN-score coalescing, node fields, flow over TRANSFER, account route."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.services.graph_service import GraphService


def test_node_data_prefers_aggregated_then_gnn_score():
    n = GraphService.node_data({"id": "a", "gnn_risk_score": 0.9, "gnn_risk_tier": "critical", "in_cycle": True})
    assert n.risk_score == 0.9 and n.risk_tier == "critical" and n.in_cycle and n.marked
    n2 = GraphService.node_data({"id": "b", "risk_score": 0.3, "gnn_risk_score": 0.9})
    assert n2.risk_score == 0.3 and n2.gnn_risk_score == 0.9       # aggregator verdict wins for display
    n3 = GraphService.node_data({"id": "c"})
    assert n3.risk_score == 0.0 and n3.risk_tier == "low" and not n3.marked
    assert n3.label == "c" and "id" not in n3.attributes


class _Res:
    def __init__(self, row): self.row = row
    async def single(self): return self.row


class _Sess:
    def __init__(self, row): self.row, self.calls = row, []
    async def run(self, q, **p):
        self.calls.append((q, p)); return _Res(self.row)
    async def __aenter__(self): return self
    async def __aexit__(self, *e): return False


def test_flow_between_reads_transfer_edges_anchored_to_activity():
    sess = _Sess({"n": 3, "total": 300})
    from app.services import stats_service
    with patch.object(stats_service.cache, "ready", lambda: True), \
         patch.object(stats_service.cache, "anchor_ts", lambda: 1_000_000), \
         patch("app.services.graph_service.neo4j_client") as nc:
        nc.driver.session = lambda: sess
        out = asyncio.run(GraphService.get_flow_between("a", "b", "24h"))
    q, p = sess.calls[0]
    assert "[t:TRANSFER]->" in q and p == {"a": "a", "b": "b", "min_ts": 1_000_000 - 86400}
    assert out["tx_count"] == 3 and out["total_volume_cents"] == 300.0 and out["avg_amount_cents"] == 100.0


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_account_route(client):
    node = GraphService.node_data({"id": "a", "gnn_risk_score": 0.7, "gnn_risk_tier": "high"})
    with patch.object(GraphService, "get_account", AsyncMock(return_value=node)):
        r = client.get("/api/graph/account/a")
    assert r.status_code == 200 and r.json()["gnn_risk_tier"] == "high"
    with patch.object(GraphService, "get_account", AsyncMock(return_value=None)):
        assert client.get("/api/graph/account/zzz").status_code == 404
