"""The viz JSON routes are also served under /api/pipeline; the HTML index is not."""
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


def test_alias_serves_threshold_and_not_index(client):
    from app.viz import threshold
    with patch.object(threshold, "model_threshold", lambda: 0.74):
        a = client.get("/api/pipeline/threshold")
        b = client.get("/viz/threshold")
    assert a.status_code == 200 and a.json() == b.json() == {"default": 0.74, "min": 0.5, "max": 0.95}
    assert client.get("/api/pipeline/").status_code == 404


def test_metrics_curve(client):
    from app.viz import metrics
    def fake_conf(cutoff):
        return {"loaded": True, "cutoff": cutoff, "precision": 1 - cutoff, "recall": cutoff,
                "tp": 1, "fp": 2, "fn": 3, "tn": 4, "marked": 3, "total": 10}
    with patch.object(metrics, "ensure_loaded", AsyncMock()), \
         patch.object(metrics, "confusion_at", fake_conf):
        r = client.get("/api/pipeline/metrics/curve?points=4")
    d = r.json()
    assert r.status_code == 200 and d["loaded"] is True
    assert d["cutoffs"] == [0.5, 0.65, 0.8, 0.95] and d["recall"] == d["cutoffs"]
    assert len(d["precision"]) == len(d["fp"]) == len(d["tp"]) == len(d["marked"]) == 4
    with patch.object(metrics, "ensure_loaded", AsyncMock()), \
         patch.object(metrics, "confusion_at", lambda c: {"loaded": False}):
        assert client.get("/api/pipeline/metrics/curve").json() == {"loaded": False}
