"""DatasetRunner: stage order, meta rewrite, truth reload, cache invalidation — all with fakes."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import dataset_runner as dr


def _run_it(patterns):
    calls = []
    pg = MagicMock(update_pipeline_run=AsyncMock(side_effect=lambda rid, **kw: calls.append(("mark", kw.get("stage"))) or None),
                   clear_risk_flags=AsyncMock(return_value=3), set_app_meta=AsyncMock())
    stats = SimpleNamespace(rows_scanned=10, min_timestamp=datetime(2022, 9, 1, tzinfo=timezone.utc),
                            max_timestamp=datetime(2022, 9, 2, tzinfo=timezone.utc), pattern_accounts=0)
    fake_pipeline = MagicMock(); fake_pipeline.run = AsyncMock(side_effect=lambda rid: calls.append(("pipeline", rid)))
    with patch.object(dr, "reset_stores", AsyncMock(side_effect=lambda n, r: calls.append(("reset", None)))), \
         patch.object(dr, "ingest_for_training", AsyncMock(return_value=stats)) as ingest, \
         patch.object(dr, "PipelineRunner", lambda *a, **k: fake_pipeline), \
         patch.object(dr.truth, "reload", lambda p: calls.append(("truth", p)) or 0), \
         patch.object(dr.stats_service, "invalidate", lambda: calls.append(("inv", "stats"))), \
         patch.object(dr.metrics, "invalidate", lambda: calls.append(("inv", "metrics"))), \
         patch.object(dr.threshold, "invalidate", lambda: calls.append(("inv", "threshold"))):
        runner = dr.DatasetRunner("NEO", "REDIS", pg, SimpleNamespace())
        asyncio.run(runner.run("rid", Path("/tmp/x.csv"), patterns, "my.csv"))
    return calls, pg, ingest


def test_stage_order_and_side_effects_unlabelled():
    calls, pg, ingest = _run_it(None)
    assert calls[:3] == [("mark", "reset"), ("reset", None), ("mark", "ingest")]
    assert ("truth", None) in calls and ("pipeline", "rid") in calls
    assert calls.index(("pipeline", "rid")) > calls.index(("inv", "stats"))
    pg.clear_risk_flags.assert_awaited_once()
    meta = pg.set_app_meta.await_args.args
    assert meta[0] == "dataset" and meta[1]["labelled"] is False and meta[1]["name"] == "my.csv"
    assert meta[1]["start_ts"] == 1661990400 and meta[1]["rows"] == 10 and meta[1]["source"] == "upload"
    assert ingest.await_args.kwargs["recompute_pagerank"] is False and ingest.await_args.kwargs["max_background_rows"] is None


def test_labelled_when_patterns_given():
    calls, pg, _ = _run_it(Path("/tmp/p.txt"))
    assert ("truth", Path("/tmp/p.txt")) in calls and pg.set_app_meta.await_args.args[1]["labelled"] is True


def test_failure_before_pipeline_marks_run_failed():
    pg = MagicMock(update_pipeline_run=AsyncMock(), clear_risk_flags=AsyncMock())
    with patch.object(dr, "reset_stores", AsyncMock(side_effect=RuntimeError("neo4j down"))):
        runner = dr.DatasetRunner("NEO", "REDIS", pg, SimpleNamespace())
        asyncio.run(runner.run("rid", Path("/tmp/x.csv"), None, "x.csv"))
    last = pg.update_pipeline_run.await_args.kwargs
    assert last["status"] == "failed" and last["stage"] == "reset" and "neo4j down" in last["error"]
