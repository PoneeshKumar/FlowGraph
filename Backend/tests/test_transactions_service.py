"""Transactions: tier max, flagged join, Cypher parameterisation, endpoint."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.services import transactions_service as ts


def test_max_tier_and_defaults():
    assert ts.max_tier("low", "critical") == "critical"
    assert ts.max_tier(None, "medium") == "medium"
    assert ts.max_tier(None, None) == "low"


def test_shape_transaction():
    rec = {"txn_id": "t1", "sender_id": "s", "receiver_id": "r", "amount_cents": 1234,
           "rail": "IBM_AML_Euro", "event_type": "SETTLEMENT", "ts": 1661991600,
           "sender_tier": "low", "receiver_tier": "high"}
    out = ts.shape_transaction(rec, lambda a: a == "r")
    assert out["currency"] == "Euro" and out["risk_tier"] == "high" and out["flagged"] is True
    assert out["sender_tier"] == "low" and out["ts"] == 1661991600


class _Res:
    def __init__(self, rows): self.rows = rows
    def __aiter__(self):
        async def g():
            for r in self.rows: yield r
        return g()


class _Sess:
    def __init__(self, rows): self.rows, self.calls = rows, []
    async def run(self, q, **p):
        self.calls.append((q, p)); return _Res(self.rows)
    async def __aenter__(self): return self
    async def __aexit__(self, *e): return False


def test_list_latest_parameterises_rail_and_limit():
    rec = {"txn_id": "t1", "sender_id": "s", "receiver_id": "r", "amount_cents": 5, "rail": "CARD",
           "event_type": None, "ts": 10, "sender_tier": None, "receiver_tier": None}
    sess = _Sess([rec])
    rows = asyncio.run(ts.list_latest(lambda: sess, limit=7, rail="CARD"))
    q, p = sess.calls[0]
    assert "t.rail = $rail" in q and p == {"limit": 7, "rail": "CARD"} and "ORDER BY t.ts DESC" in q
    assert "t.ts IS NOT NULL" in q                      # index-backed ordering (see list_latest)
    assert rows[0]["risk_tier"] == "low" and rows[0]["flagged"] is False
    sess2 = _Sess([])
    asyncio.run(ts.list_latest(lambda: sess2, limit=3, rail=None))
    assert "$rail" not in sess2.calls[0][0] and "t.ts IS NOT NULL" in sess2.calls[0][0]


def test_list_for_account_uses_both_directions():
    sess = _Sess([])
    asyncio.run(ts.list_for_account(lambda: sess, "acc", limit=5, rail=None))
    q, p = sess.calls[0]
    assert "-[t:TRANSFER]-(" in q and "startNode(t)" in q and p == {"id": "acc", "limit": 5}


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_transactions_endpoint(client):
    canned = [{"txn_id": "t", "sender_id": "s", "receiver_id": "r", "amount_cents": 1, "currency": "CARD",
               "rail": "CARD", "event_type": None, "ts": 1, "sender_tier": "low", "receiver_tier": "low",
               "risk_tier": "low", "flagged": False}]
    with patch.object(ts, "list_latest", AsyncMock(return_value=canned)) as m:
        r = client.get("/api/transactions?limit=5")
    assert r.status_code == 200 and r.json()["items"][0]["txn_id"] == "t"
    assert m.await_args.kwargs["limit"] == 5
    with patch.object(ts, "list_for_account", AsyncMock(return_value=[])) as m2:
        r = client.get("/api/transactions?account_id=abc&limit=3")
    assert r.status_code == 200 and m2.await_args.args[1] == "abc"
    assert client.get("/api/transactions?limit=999").status_code == 422
