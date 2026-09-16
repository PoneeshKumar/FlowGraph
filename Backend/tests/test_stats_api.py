"""Stats endpoints: warming → 503, ready → cache reads, unknown currency → 404."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_overview_503_while_warming(client):
    from app.services import stats_service
    stats_service.cache.invalidate()
    r = client.get("/api/stats/overview")
    assert r.status_code == 503 and "warming" in r.json()["detail"]


def test_overview_and_series_when_ready(client):
    from app.services import stats_service
    c = stats_service.cache
    c._hist = {"IBM_AML_US Dollar": {0: (2, 10_000)}}
    c._counts = {"accounts": 1, "flows": 1, "transactions": 2, "scored_accounts": 1,
                 "communities": 1, "in_cycle": 0}
    c._dataset = {"start_ts": 0, "end_ts": 3600, "labelled": True}
    c._open_flags, c._flagged, c._latest_run, c._computed_at = {"AGGREGATE": 1}, set(), None, 1
    try:
        r = client.get("/api/stats/overview")
        assert r.status_code == 200 and r.json()["open_flags"]["total"] == 1
        r = client.get("/api/stats/volume-series?currency=US%20Dollar&period=24h")
        assert r.status_code == 200 and r.json()["volume"][-1] == 100.0
        assert client.get("/api/stats/volume-series?currency=Nope").status_code == 404
        assert client.get("/api/stats/volume-series?currency=US%20Dollar&period=90d").status_code == 422
    finally:
        c.invalidate()
