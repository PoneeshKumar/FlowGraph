"""End-to-end read-path smoke against the live stack (Neo4j + Postgres with the
IBM graph loaded). Skips unless POSTGRES_DSN points at the dev container:

    POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
        python3 -m pytest tests/test_api_smoke.py -v
"""
import os
import time
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    "changeme@" not in os.environ.get("POSTGRES_DSN", ""),
    reason="live stack not configured (POSTGRES_DSN)")


@pytest.fixture(scope="module")
def client():
    from app.api.main import app
    try:
        with TestClient(app) as c:
            deadline = time.time() + 120
            while c.get("/api/stats/overview").status_code == 503 and time.time() < deadline:
                time.sleep(2)
            yield c
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"live stack unavailable: {exc}")


def test_overview_and_series(client):
    ov = client.get("/api/stats/overview").json()
    assert ov["transactions"] > 1_000_000 and ov["currencies"][0]["code"] == "US Dollar"
    assert ov["dataset"]["end_ts"] == 1663517880
    s = client.get("/api/stats/volume-series?currency=US%20Dollar&period=all").json()
    assert s["bucket_seconds"] == 86400 and s["volume"][-1] > 0


def test_alerts_transactions_graph_chain(client):
    alerts = client.get("/api/alerts?limit=3&min_level=high").json()
    assert alerts["total"] > 0 and alerts["items"][0]["explanation"]
    acct = alerts["items"][0]["primary_account"]
    node = client.get(f"/api/graph/account/{acct}").json()
    assert node["id"] == acct and node["gnn_risk_score"] is not None
    tx = client.get(f"/api/transactions?account_id={acct}&limit=5").json()["items"]
    assert tx and all(t["ts"] > 0 for t in tx)
    latest = client.get("/api/transactions?limit=5").json()["items"]
    assert [t["ts"] for t in latest] == sorted((t["ts"] for t in latest), reverse=True)
    sub = client.get(f"/api/graph/subgraph?account_id={acct}&depth=1").json()
    assert sub["nodes"] and any(n["data"]["risk_tier"] != "low" for n in sub["nodes"])


def test_pipeline_alias_and_llm(client):
    assert client.get("/api/pipeline/threshold").json()["default"] >= 0.5
    curve = client.get("/api/pipeline/metrics/curve?points=5").json()
    assert curve["loaded"] is True and len(curve["cutoffs"]) == 5
    llm = client.get("/api/system/llm").json()
    assert llm["provider"] in ("none", "ollama", "anthropic")
    acct = client.get("/api/alerts?limit=1").json()["items"][0]["primary_account"]
    ex = client.get(f"/api/accounts/{acct}/enrich").json()
    assert ex["account_id"] == acct and ex["explanation"] and ex["risk_level"] in ("low", "medium", "high", "critical")
