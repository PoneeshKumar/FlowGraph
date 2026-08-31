"""API tests for the /viz router.

The deps lifespan (which connects the real Neo4j/Postgres clients) is patched to
no-ops so these run without databases; each test mocks the store/deps calls it
exercises.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_communities_endpoint(client):
    from app.viz import store
    canned = [{"community_id": "c1", "size": 12, "risk_score": 0.7,
               "risk_tier": "high", "flagged_count": 4}]
    with patch.object(store, "list_communities", AsyncMock(return_value=canned)):
        r = client.get("/viz/communities?sort=risk")
    assert r.status_code == 200 and r.json()[0]["community_id"] == "c1"


def test_subgraph_requires_a_selector(client):
    r = client.get("/viz/subgraph")
    assert r.status_code == 422


def test_subgraph_calls_store(client):
    from app.viz import store
    payload = {"nodes": [], "edges": [], "truncated": {"shown": 0, "total": 0}}
    with patch.object(store, "load_subgraph", AsyncMock(return_value=payload)) as m:
        r = client.get("/viz/subgraph?account_id=acc_x&hops=3")
    assert r.status_code == 200
    assert m.await_args.kwargs["account_id"] == "acc_x" and m.await_args.kwargs["hops"] == 3


def test_marked_endpoint(client):
    from app.viz import store
    canned = [{"account_id": "x", "combined_score": 0.9,
               "signals": {"gnn": True, "cycle": False, "community": False},
               "gnn_score": 0.9, "community_id": None, "in_cycle": False, "rationale": "r"}]
    with patch.object(store, "list_marked", AsyncMock(return_value=canned)):
        r = client.get("/viz/marked")
    assert r.status_code == 200 and r.json()[0]["account_id"] == "x"


def test_run_conflict_when_active(client):
    from app.viz import deps
    mock_pg = MagicMock(get_active_pipeline_run=AsyncMock(return_value={"id": "x"}))
    with patch.object(deps, "pg", lambda: mock_pg):
        r = client.post("/viz/run")
    assert r.status_code == 409


def test_run_starts_when_idle(client):
    from app.viz import deps
    mock_pg = MagicMock(
        get_active_pipeline_run=AsyncMock(return_value=None),
        create_pipeline_run=AsyncMock(return_value="RID"),
    )
    with patch.object(deps, "pg", lambda: mock_pg), \
         patch.object(deps, "neo4j", lambda: MagicMock()), \
         patch("app.viz.router.PipelineRunner") as PR:
        PR.return_value.run = AsyncMock()
        r = client.post("/viz/run")
    assert r.status_code == 200 and r.json()["run_id"] == "RID"


def test_run_status_404(client):
    from app.viz import deps
    mock_pg = MagicMock(get_pipeline_run=AsyncMock(return_value=None))
    with patch.object(deps, "pg", lambda: mock_pg):
        r = client.get("/viz/run/nope")
    assert r.status_code == 404
