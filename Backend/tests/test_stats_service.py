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
