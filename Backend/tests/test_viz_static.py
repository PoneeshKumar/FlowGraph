"""The viewer page + static assets are served. DB-decoupled (lifespan patched)."""
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


def test_index_served(client):
    r = client.get("/viz/")
    assert r.status_code == 200
    assert 'id="cy"' in r.text and 'id="tabs"' in r.text


def test_static_assets_served(client):
    assert client.get("/viz/static/vendor/cytoscape.min.js").status_code == 200
    assert client.get("/viz/static/app.js").status_code == 200
    assert client.get("/viz/static/styles.css").status_code == 200
