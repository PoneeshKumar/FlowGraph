"""Unit tests for PipelineRunner with all underlying services mocked."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.viz.runner import PipelineRunner, STAGES


def _settings():
    s = MagicMock()
    s.GNN_RUN_DIR = "ml/runs/v10_L3"
    s.GNN_ENSEMBLE_RUNS = []
    s.GNN_FEATURE_CACHE = "ml/cache/featureset_v4.npz"
    s.MARK_GNN_THRESHOLD = 0.5
    s.CYCLE_MAX_SEEDS = 500
    return s


def test_stage_list():
    assert STAGES == ["pagerank", "louvain", "cycle", "gnn", "aggregate"]


@pytest.mark.asyncio
async def test_gnn_uses_present_ensemble_members_and_skips_missing():
    # The ensemble members are optional artifacts — a member that isn't on disk
    # must be dropped, so serving degrades to the champion instead of crashing.
    neo4j = MagicMock(write_gnn_scores=AsyncMock())
    pg = MagicMock(update_pipeline_run=AsyncMock())
    s = _settings()
    s.GNN_ENSEMBLE_RUNS = ["ml/runs/v10_L3_s1", "ml/runs/v10_L3_missing"]
    feature_set = MagicMock(node_ids=["a", "b"])

    def fake_exists(self):
        return "missing" not in str(self)     # champion, cache, s1 exist; "_missing" doesn't

    with patch("app.viz.runner.load_feature_cache", return_value=feature_set), \
         patch("app.viz.runner.ensemble_scores", return_value=[0.9, 0.1]) as ES, \
         patch("pathlib.Path.exists", fake_exists):
        runner = PipelineRunner(neo4j, pg, s)
        await runner._gnn("RID")

    used = [d.name for d in ES.call_args[0][0]]
    assert "v10_L3" in used and "v10_L3_s1" in used      # champion + present member
    assert "v10_L3_missing" not in used                  # absent member dropped
    neo4j.write_gnn_scores.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_executes_all_stages_and_completes():
    neo4j = MagicMock(
        recompute_pagerank_full=AsyncMock(return_value=10),
        write_gnn_scores=AsyncMock(return_value=2),
        write_cycle_membership=AsyncMock(return_value=1),
    )
    pg = MagicMock(update_pipeline_run=AsyncMock(), upsert_risk_flag=AsyncMock())
    feature_set = MagicMock(node_ids=["a", "b"])

    with patch("app.viz.runner.CommunityDetector") as CD, \
         patch("app.viz.runner.CycleDetector") as CY, \
         patch("app.viz.runner.load_feature_cache", return_value=feature_set), \
         patch("app.viz.runner.ensemble_scores", return_value=[0.9, 0.1]), \
         patch("pathlib.Path.exists", return_value=True), \
         patch.object(PipelineRunner, "_cycle_window", AsyncMock(return_value=(None, None))), \
         patch.object(PipelineRunner, "_cycle_seeds", AsyncMock(return_value=["a"])):
        CD.return_value.run = AsyncMock(return_value={"communities": 5})
        CY.return_value.detect = AsyncMock(return_value=[{"account_ids": ["a"]}])
        runner = PipelineRunner(neo4j, pg, _settings())
        await runner.run("RID")

    last = pg.update_pipeline_run.await_args_list[-1].kwargs
    assert last["status"] == "completed" and last["finished"] is True
    assert last["counts"]["marked"] == 1          # 'a' fires gnn+cycle; 'b' fires nothing
    neo4j.recompute_pagerank_full.assert_awaited_once()
    neo4j.write_gnn_scores.assert_awaited_once()
    neo4j.write_cycle_membership.assert_awaited_once()
    pg.upsert_risk_flag.assert_awaited_once()      # only the marked account


@pytest.mark.asyncio
async def test_cycle_forwards_anchored_window_to_detector():
    # The data-anchored window (ref, hours) must reach detect() — otherwise the
    # default "last 48h from now" excludes every edge in the historical dataset.
    from datetime import datetime, timezone
    ref = datetime(2026, 8, 30, tzinfo=timezone.utc)
    neo4j = MagicMock(write_cycle_membership=AsyncMock(return_value=2))
    pg = MagicMock(update_pipeline_run=AsyncMock())
    with patch("app.viz.runner.CycleDetector") as CY, \
         patch.object(PipelineRunner, "_cycle_window", AsyncMock(return_value=(ref, 35107))), \
         patch.object(PipelineRunner, "_cycle_seeds", AsyncMock(return_value=["s1"])):
        CY.return_value.detect = AsyncMock(return_value=[{"account_ids": ["s1", "s2"]}])
        runner = PipelineRunner(neo4j, pg, _settings())
        members = await runner._cycle("RID")
    CY.return_value.detect.assert_awaited_once_with(
        "s1", reference_time=ref, window_hours=35107)
    assert members == {"s1", "s2"}
    neo4j.write_cycle_membership.assert_awaited_once_with({"s1", "s2"})


@pytest.mark.asyncio
async def test_cycle_window_anchors_just_past_newest_edge():
    # min last_ts and max last_ts an hour apart → window spans that hour + 48h margin,
    # anchored one hour past the newest edge.
    mn, mx = 1_000_000, 1_003_600            # 3600s = 1h apart
    session = MagicMock()
    session.run = AsyncMock(return_value=MagicMock(
        single=AsyncMock(return_value={"mn": mn, "mx": mx})))
    cm = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock())
    neo4j = MagicMock()
    neo4j.driver.session = MagicMock(return_value=cm)
    runner = PipelineRunner(neo4j, MagicMock(), _settings())
    ref, hours = await runner._cycle_window()
    assert int(ref.timestamp()) == mx + 3600     # just past the newest edge
    assert hours == (mx - mn) // 3600 + 48        # 1 + 48


@pytest.mark.asyncio
async def test_cycle_window_none_when_no_timestamps():
    session = MagicMock()
    session.run = AsyncMock(return_value=MagicMock(
        single=AsyncMock(return_value={"mn": None, "mx": None})))
    cm = MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock())
    neo4j = MagicMock()
    neo4j.driver.session = MagicMock(return_value=cm)
    runner = PipelineRunner(neo4j, MagicMock(), _settings())
    assert await runner._cycle_window() == (None, None)


@pytest.mark.asyncio
async def test_stage_failure_records_failed():
    neo4j = MagicMock(recompute_pagerank_full=AsyncMock(side_effect=RuntimeError("boom")))
    pg = MagicMock(update_pipeline_run=AsyncMock())
    runner = PipelineRunner(neo4j, pg, _settings())
    await runner.run("RID")
    last = pg.update_pipeline_run.await_args_list[-1].kwargs
    assert last["status"] == "failed"
    assert last["stage"] == "pagerank" and "boom" in last["error"]


@pytest.mark.asyncio
async def test_gnn_skips_gracefully_when_artifacts_missing():
    neo4j = MagicMock(recompute_pagerank_full=AsyncMock(),
                      write_gnn_scores=AsyncMock(), write_cycle_membership=AsyncMock())
    pg = MagicMock(update_pipeline_run=AsyncMock(), upsert_risk_flag=AsyncMock())
    with patch("app.viz.runner.CommunityDetector") as CD, \
         patch("app.viz.runner.CycleDetector") as CY, \
         patch("pathlib.Path.exists", return_value=False), \
         patch.object(PipelineRunner, "_cycle_window", AsyncMock(return_value=(None, None))), \
         patch.object(PipelineRunner, "_cycle_seeds", AsyncMock(return_value=["a"])):
        CD.return_value.run = AsyncMock(return_value={})
        CY.return_value.detect = AsyncMock(return_value=[{"account_ids": ["a"]}])
        runner = PipelineRunner(neo4j, pg, _settings())
        await runner.run("RID")
    last = pg.update_pipeline_run.await_args_list[-1].kwargs
    assert last["status"] == "completed"            # completes without the GNN
    neo4j.write_gnn_scores.assert_not_awaited()      # GNN stage skipped
    assert last["counts"]["marked"] == 1             # 'a' still marked via cycle signal
