"""Pure tests for the stats series math (no stores)."""
import pytest

from app.services import stats_service as ss


def test_currency_code_strips_ibm_prefix_only():
    assert ss.currency_code("IBM_AML_US Dollar") == "US Dollar"
    assert ss.currency_code("CARD") == "CARD"


def test_period_window_anchors_to_dataset_end():
    start, end, bucket = ss.period_window("24h", 1000, 100000)
    assert (start, end, bucket) == (100000 - 86400, 100000, 1800)
    start, end, bucket = ss.period_window("7d", 1000, 100000 + 604800)
    assert (start, end, bucket) == (100000, 100000 + 604800, 21600)
    start, end, bucket = ss.period_window("all", 1000, 100000)
    assert (start, end, bucket) == (1000, 100000, 86400)
    with pytest.raises(ValueError):
        ss.period_window("90d", 0, 1)


def test_build_series_is_cumulative_with_uniform_baseline():
    # two base buckets (30 min each) inside a 1 h window bucketed at 1800 s
    start, end, bucket = 0, 3600, 1800
    hist = {0: (2, 10_000), 1: (3, 20_000)}          # ts 0..1799 and 1800..3599
    out = ss.build_series(hist, start, end, bucket)
    assert out["t"] == [0, 1800, 3600]
    assert out["volume"] == [0.0, 100.0, 300.0]       # cents → major units, cumulative
    assert out["txns"] == [0, 2, 5]
    assert out["baseline"] == [0.0, 150.0, 300.0]     # straight line to the final total


def test_build_series_ignores_buckets_outside_window():
    hist = {0: (1, 100), 10: (1, 100), 999: (1, 100)}
    out = ss.build_series(hist, start_ts=0, end_ts=3600, bucket_seconds=1800)
    assert out["txns"][-1] == 1 and out["volume"][-1] == 1.0


def test_build_series_single_point_window():
    out = ss.build_series({}, start_ts=5, end_ts=5, bucket_seconds=1800)
    assert out["t"] == [5] and out["volume"] == [0.0] and out["baseline"] == [0.0]


# ---- StatsCache with fake stores --------------------------------------------
import asyncio
from unittest.mock import AsyncMock, MagicMock


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    async def single(self):
        return self._rows[0] if self._rows else None

    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


class _FakeSession:
    """Answers each Cypher query by a substring match on the query text."""
    def __init__(self, answers):
        self.answers = answers

    async def run(self, query, **params):
        for needle, rows in self.answers:
            if needle in query:
                return _FakeResult(rows)
        raise AssertionError("unexpected query: " + query)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_session(answers):
    return lambda: _FakeSession(answers)


def _fake_pg():
    return MagicMock(
        get_app_meta=AsyncMock(return_value={"name": "T", "source": "ibm-hi-small", "labelled": True,
                                             "start_ts": 0, "end_ts": 7200}),
        count_open_flags_by_type=AsyncMock(return_value={"AGGREGATE": 2, "CYCLE": 1}),
        get_account_ids_for_flag_type=AsyncMock(return_value=["f1", "f2"]),
        get_latest_pipeline_run=AsyncMock(return_value={"id": "r1", "status": "completed"}),
    )


def _answers():
    return [
        ("toInteger(t.ts / 1800)", [
            {"rail": "IBM_AML_US Dollar", "b": 0, "n": 2, "total": 10_000},
            {"rail": "IBM_AML_US Dollar", "b": 1, "n": 3, "total": 20_000},
            {"rail": "IBM_AML_Euro", "b": 2, "n": 1, "total": 5_000},
        ]),
        ("MATCH (a:Account) WHERE a.gnn_risk_score IS NOT NULL", [{"n": 9}]),
        ("count(DISTINCT a.community_id)", [{"n": 4}]),
        ("a.in_cycle = true", [{"n": 1}]),
        ("MATCH ()-[t:TRANSFER]->() RETURN count(t)", [{"n": 6}]),
        ("MATCH ()-[r:FLOWS_TO]->() RETURN count(r)", [{"n": 5}]),
        ("MATCH (a:Account) RETURN count(a)", [{"n": 10}]),
    ]


def test_cache_builds_overview_and_series():
    c = ss.StatsCache()
    assert not c.ready()
    asyncio.run(c.build(_fake_session(_answers()), _fake_pg()))
    assert c.ready()
    ov = c.overview()
    assert ov["accounts"] == 10 and ov["flows"] == 5 and ov["transactions"] == 6
    assert ov["scored_accounts"] == 9 and ov["communities"] == 4 and ov["in_cycle"] == 1
    assert ov["open_flags"] == {"AGGREGATE": 2, "CYCLE": 1, "total": 3}
    usd = next(x for x in ov["currencies"] if x["code"] == "US Dollar")
    assert usd["tx_count"] == 5 and usd["volume"] == 300.0 and usd["rail"] == "IBM_AML_US Dollar"
    assert ov["currencies"][0]["code"] == "US Dollar"          # sorted by tx_count desc
    assert ov["dataset"]["end_ts"] == 7200 and ov["latest_run"]["id"] == "r1"
    s = c.series("US Dollar", "all")
    assert s["currency"] == "US Dollar" and s["anchor_ts"] == 7200 and s["start_ts"] == 0
    assert s["volume"][-1] == 300.0 and s["txns"][-1] == 5
    # short periods anchor at the end of observed activity (last bucket b=2 → 5400)
    s7 = c.series("US Dollar", "7d")
    assert s7["anchor_ts"] == 5400 and c.anchor_ts() == 5400
    assert ov["dataset"]["activity_end_ts"] == 5400
    assert c.is_flagged("f1") and not c.is_flagged("zzz")
    assert c.rail_for("Euro") == "IBM_AML_Euro" and c.rail_for("Nope") is None
    assert c.dataset_end_ts() == 7200


def test_activity_end_ts_ignores_sparse_tail():
    hist = {"A": {0: (1000, 1), 5: (1, 1)}}            # 1000 txns in bucket 0, 1 straggler later
    assert ss.activity_end_ts(hist, dataset_end=99_999) == 1800
    assert ss.activity_end_ts({"A": {}}, dataset_end=42) == 42     # no data → dataset end
    assert ss.activity_end_ts({"A": {3: (5, 1)}}, dataset_end=5000) == 5000  # clamped to end


def test_cache_series_errors_and_invalidate():
    c = ss.StatsCache()
    asyncio.run(c.build(_fake_session(_answers()), _fake_pg()))
    with pytest.raises(KeyError):
        c.series("Nope", "7d")
    with pytest.raises(ValueError):
        c.series("Euro", "90d")
    c.invalidate()
    assert not c.ready()


def test_cache_dataset_bounds_fall_back_to_histogram():
    pg = _fake_pg()
    pg.get_app_meta = AsyncMock(return_value=None)
    c = ss.StatsCache()
    asyncio.run(c.build(_fake_session(_answers()), pg))
    ds = c.overview()["dataset"]
    assert ds["start_ts"] == 0 and ds["end_ts"] == 3 * 1800 and ds["labelled"] is False


def test_warmup_skips_without_pg():
    asyncio.run(ss.warmup(_fake_session([]), None))   # must not raise
