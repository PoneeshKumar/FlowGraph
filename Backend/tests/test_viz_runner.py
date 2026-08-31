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
    return s


def test_stage_list():
    assert STAGES == ["pagerank", "louvain", "cycle", "gnn", "aggregate"]


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
         patch("app.viz.runner.ensemble_scores", return_value=[0.9, 0.1]):
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
async def test_stage_failure_records_failed():
    neo4j = MagicMock(recompute_pagerank_full=AsyncMock(side_effect=RuntimeError("boom")))
    pg = MagicMock(update_pipeline_run=AsyncMock())
    runner = PipelineRunner(neo4j, pg, _settings())
    await runner.run("RID")
    last = pg.update_pipeline_run.await_args_list[-1].kwargs
    assert last["status"] == "failed"
    assert last["stage"] == "pagerank" and "boom" in last["error"]
