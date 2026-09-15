"""Alerts: shaping, list with total, status update, validation."""
import asyncio
from datetime import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.services import alerts_service


def _row(**over):
    base = {"id": 7, "flag_type": "AGGREGATE", "fingerprint": "agg:x", "account_ids": ["x", "y"],
            "risk_level": "high", "risk_score": 0.81, "explanation": "why",
            "details": '{"signals": {"gnn": true}}', "status": "open",
            "first_detected_at": datetime(2026, 9, 1), "last_detected_at": datetime(2026, 9, 2),
            "detection_count": 3, "created_at": datetime(2026, 9, 1)}
    base.update(over)
    return base


def test_shape_alert_parses_details_and_primary_account():
    a = alerts_service.shape_alert(_row())
    assert a["primary_account"] == "x" and a["details"] == {"signals": {"gnn": True}}
    assert a["first_detected_at"].startswith("2026-09-01") and a["risk_score"] == 0.81
    assert alerts_service.shape_alert(_row(account_ids=[], details=None))["primary_account"] is None


def test_list_alerts_returns_total_and_items():
    pg = MagicMock(get_risk_flags=AsyncMock(return_value=[_row()]),
                   count_risk_flags=AsyncMock(return_value=42))
    out = asyncio.run(alerts_service.list_alerts(pg, "AGGREGATE", "medium", "open", 10, 20))
    assert out["total"] == 42 and out["items"][0]["id"] == 7
    assert pg.get_risk_flags.await_args.kwargs == {"flag_type": "AGGREGATE", "min_level": "medium",
                                                    "status": "open", "limit": 10, "offset": 20}


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_alerts_endpoint(client):
    from app.viz import deps
    pg = MagicMock(get_risk_flags=AsyncMock(return_value=[_row()]),
                   count_risk_flags=AsyncMock(return_value=1))
    with patch.object(deps, "pg", lambda: pg):
        r = client.get("/api/alerts?flag_type=AGGREGATE&min_level=high&limit=5")
    assert r.status_code == 200
    assert r.json()["total"] == 1 and r.json()["items"][0]["primary_account"] == "x"
    assert client.get("/api/alerts?limit=9999").status_code == 422
    assert client.get("/api/alerts?min_level=severe").status_code == 422


def test_alert_status_update(client):
    from app.viz import deps
    pg = MagicMock(update_risk_flag_status=AsyncMock(return_value=_row(status="reviewed")))
    with patch.object(deps, "pg", lambda: pg):
        r = client.patch("/api/alerts/7/status", json={"status": "reviewed"})
        assert r.status_code == 200 and r.json()["status"] == "reviewed"
        assert client.patch("/api/alerts/7/status", json={"status": "bogus"}).status_code == 422
    pg2 = MagicMock(update_risk_flag_status=AsyncMock(return_value=None))
    with patch.object(deps, "pg", lambda: pg2):
        assert client.patch("/api/alerts/999/status", json={"status": "dismissed"}).status_code == 404
