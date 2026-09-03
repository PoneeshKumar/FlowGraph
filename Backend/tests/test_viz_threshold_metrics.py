"""Tests for the tuned-threshold + whole-graph metrics feature.

Pure metrics maths and the threshold loader run without a DB. The API routes are
exercised with the deps lifespan patched out (as in test_viz_api).
"""
import json
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.viz import metrics, threshold


# ---- metrics.confusion_at (pure over the cached arrays) -------------------

def _seed_metrics(scores, in_cycle, labels):
    metrics._scores = np.asarray(scores, dtype=float)
    metrics._in_cycle = np.asarray(in_cycle, dtype=bool)
    metrics._labels = np.asarray(labels, dtype=bool)


def test_confusion_at_counts_and_rates():
    # scores:  0.9   0.6   0.8   0.2
    # truth:    F     T     T     F
    _seed_metrics([0.9, 0.6, 0.8, 0.2], [False, False, False, False],
                  [False, True, True, False])
    m = metrics.confusion_at(0.75)          # marks the two ≥0.75 (idx 0,2)
    assert m["marked"] == 2 and m["tp"] == 1 and m["fp"] == 1
    assert m["fn"] == 1 and m["tn"] == 1
    assert m["precision"] == 0.5 and m["recall"] == 0.5


def test_confusion_at_cycle_marks_below_cutoff():
    # idx1 is below cutoff but on a cycle → still marked (matches the graph tabs)
    _seed_metrics([0.2, 0.3], [False, True], [False, True])
    m = metrics.confusion_at(0.75)
    assert m["marked"] == 1 and m["tp"] == 1 and m["recall"] == 1.0


def test_confusion_at_reports_unloaded():
    metrics.invalidate()
    assert metrics.confusion_at(0.7) == {"loaded": False}


# ---- threshold.model_threshold -------------------------------------------

def test_model_threshold_reads_run_json(tmp_path):
    threshold._cached = None
    run = tmp_path / "run"; run.mkdir()
    # A real result.json always carries the persisted scaler state (see
    # ml/predict.py:load_run) — a run missing it predates scaler persistence
    # and is unusable for inference, so the loader falls back instead.
    (run / "result.json").write_text(json.dumps({"threshold": 0.7376, "scaler": {"kind": "quantile"}}))
    with patch.object(threshold.settings, "GNN_RUN_DIR", str(run)):
        assert abs(threshold.model_threshold() - 0.7376) < 1e-9
    threshold._cached = None


def test_model_threshold_falls_back_when_scaler_missing(tmp_path):
    threshold._cached = None
    run = tmp_path / "run"; run.mkdir()
    (run / "result.json").write_text(json.dumps({"threshold": 0.7376}))
    with patch.object(threshold.settings, "GNN_RUN_DIR", str(run)), \
         patch.object(threshold.settings, "MARK_GNN_THRESHOLD", 0.5):
        assert threshold.model_threshold() == 0.5
    threshold._cached = None


def test_model_threshold_falls_back_when_missing(tmp_path):
    threshold._cached = None
    with patch.object(threshold.settings, "GNN_RUN_DIR", str(tmp_path / "nope")), \
         patch.object(threshold.settings, "MARK_GNN_THRESHOLD", 0.5):
        assert threshold.model_threshold() == 0.5
    threshold._cached = None


# ---- API routes ----------------------------------------------------------

@pytest.fixture
def client():
    from app.viz import deps as viz_deps
    with patch.object(viz_deps, "startup", AsyncMock()), \
         patch.object(viz_deps, "shutdown", AsyncMock()):
        from app.api.main import app
        with TestClient(app) as c:
            yield c


def test_threshold_endpoint(client):
    threshold._cached = 0.7376
    r = client.get("/viz/threshold")
    threshold._cached = None
    assert r.status_code == 200
    body = r.json()
    assert abs(body["default"] - 0.7376) < 1e-9
    assert body["min"] == threshold.MIN_CUTOFF and body["max"] == threshold.MAX_CUTOFF


def test_metrics_endpoint(client):
    _seed_metrics([0.9, 0.6], [False, False], [True, False])
    with patch.object(metrics, "ensure_loaded", AsyncMock()):
        r = client.get("/viz/metrics?cutoff=0.75")
    assert r.status_code == 200
    m = r.json()
    assert m["loaded"] is True and m["tp"] == 1 and m["marked"] == 1


def test_metrics_endpoint_rejects_out_of_range(client):
    r = client.get("/viz/metrics?cutoff=1.5")
    assert r.status_code == 422
