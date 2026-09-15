"""Upload endpoints. Every rejection happens before any store is touched, so the
tests never reset anything: the runner is replaced by a fake that records its args."""
import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.services.dataset_validation import template_csv


@pytest.fixture
def client(tmp_path):
    from app.viz import deps as viz_deps
    from app.core.config import settings
    with patch.object(viz_deps, "startup", AsyncMock()), patch.object(viz_deps, "shutdown", AsyncMock()), \
         patch.object(settings, "UPLOAD_DIR", str(tmp_path / "uploads")):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_template_and_current(client):
    r = client.get("/api/datasets/template")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("Timestamp,From Bank,Account,")
    from app.viz import deps
    with patch.object(deps, "pg", lambda: MagicMock(get_app_meta=AsyncMock(return_value={"name": "X", "source": "upload"}))):
        assert client.get("/api/datasets/current").json()["name"] == "X"
    with patch.object(deps, "pg", lambda: MagicMock(get_app_meta=AsyncMock(return_value=None))):
        assert client.get("/api/datasets/current").status_code == 404


def _files(text):
    return {"transactions": ("t.csv", io.BytesIO(text.encode()), "text/csv")}


def test_upload_requires_confirm_and_valid_csv(client):
    from app.viz import deps
    pg = MagicMock(get_active_pipeline_run=AsyncMock(return_value=None))
    with patch.object(deps, "pg", lambda: pg):
        assert client.post("/api/datasets/upload", files=_files(template_csv())).status_code == 422
        assert client.post("/api/datasets/upload", files=_files(template_csv()), data={"confirm": "yes"}).status_code == 422
        r = client.post("/api/datasets/upload", files=_files("a,b\n1,2\n"), data={"confirm": "replace"})
        assert r.status_code == 422 and "Timestamp" in r.json()["detail"]
    pg.create_pipeline_run.assert_not_called()


def test_upload_conflicts_when_run_active(client):
    from app.viz import deps
    pg = MagicMock(get_active_pipeline_run=AsyncMock(return_value={"id": "busy"}))
    with patch.object(deps, "pg", lambda: pg):
        r = client.post("/api/datasets/upload", files=_files(template_csv()), data={"confirm": "replace"})
    assert r.status_code == 409


def test_upload_launches_runner_with_saved_files(client, tmp_path):
    from app.viz import deps
    pg = MagicMock(get_active_pipeline_run=AsyncMock(return_value=None), create_pipeline_run=AsyncMock(return_value="RID"))
    launched = {}

    class FakeRunner:
        def __init__(self, *a, **k): pass
        async def run(self, run_id, csv_path, patterns_path, name):
            launched.update(run_id=run_id, csv=csv_path, patterns=patterns_path, name=name)

    with patch.object(deps, "pg", lambda: pg), patch.object(deps, "neo4j", lambda: MagicMock()), \
         patch.object(deps, "redis", lambda: MagicMock()), \
         patch("app.api.endpoints.DatasetRunner", FakeRunner):
        files = dict(_files(template_csv()), patterns=("p.txt", io.BytesIO(b"BEGIN LAUNDERING ATTEMPT - CYCLE\n"), "text/plain"))
        r = client.post("/api/datasets/upload", files=files, data={"confirm": "replace"})
    assert r.status_code == 200 and r.json()["run_id"] == "RID"
    assert launched["run_id"] == "RID" and launched["name"] == "t.csv"
    assert launched["csv"].exists() and launched["patterns"].exists() and "RID" in str(launched["csv"])
