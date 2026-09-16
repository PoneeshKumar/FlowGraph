"""CORS policy: loopback on any port is allowed (Vite moves ports on its own),
everything else must be listed explicitly — never a credentialed wildcard."""
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


@pytest.mark.parametrize("origin", [
    "http://localhost:5173", "http://localhost:5176", "http://127.0.0.1:3000", "https://localhost:8080",
])
def test_loopback_origins_allowed_on_any_port(client, origin):
    r = client.get("/health", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("origin", [
    "http://evil.example.com", "http://localhost.evil.com", "http://notlocalhost:5173",
])
def test_other_origins_are_not_allowed(client, origin):
    r = client.get("/health", headers={"Origin": origin})
    assert r.headers.get("access-control-allow-origin") is None


def test_no_credentialed_wildcard(client):
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") != "*"
    assert r.headers.get("access-control-allow-credentials") == "true"
