"""Whole-graph statistics for the dashboard (spec §6.1–6.3).

A single Cypher pass over TRANSFER builds a (rail, 30-minute bucket) histogram;
every currency/period series derives from it in memory. Everything here that
does arithmetic is a pure function so it can be unit-tested without stores.
"""
import asyncio
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("stats")

BUCKET_BASE = 1800   # seconds; the histogram's resolution

# period → (window length in seconds or None for the whole dataset, bucket seconds)
PERIODS: Dict[str, Tuple[Optional[int], int]] = {
    "24h": (86400, 1800),
    "7d": (604800, 21600),
    "all": (None, 86400),
}

_IBM_PREFIX = "IBM_AML_"


def currency_code(rail: str) -> str:
    """Display code for a rail. IBM AML rails are `IBM_AML_<currency>`; any
    other rail string (CARD, WIRE, …) is shown verbatim."""
    if rail.startswith(_IBM_PREFIX):
        return rail[len(_IBM_PREFIX):]
    return rail


def period_window(period: str, dataset_start: int, dataset_end: int) -> Tuple[int, int, int]:
    """(start_ts, end_ts, bucket_seconds) for a period, anchored to the dataset's
    last timestamp — never wall-clock, the data is historical."""
    if period not in PERIODS:
        raise ValueError("period must be one of 24h, 7d, all")
    length, bucket = PERIODS[period]
    start = dataset_start if length is None else max(dataset_start, dataset_end - length)
    return start, dataset_end, bucket


def build_series(hist: Dict[int, Tuple[int, int]], start_ts: int, end_ts: int,
                 bucket_seconds: int) -> Dict[str, List[Any]]:
    """Cumulative volume/txn series over [start_ts, end_ts] plus a uniform-pace
    baseline. Point i sits at start_ts + i*bucket and holds everything that
    happened before it; point 0 is always zero."""
    span = max(0, end_ts - start_ts)
    n = int(math.ceil(span / bucket_seconds)) + 1 if span > 0 else 1
    t = [start_ts + i * bucket_seconds for i in range(n)]
    vol = [0.0] * n
    cnt = [0] * n
    for b, (count, amount_cents) in hist.items():
        ts = b * BUCKET_BASE
        if ts < start_ts or ts >= end_ts:
            continue
        i = min(n - 1, int((ts - start_ts) // bucket_seconds) + 1)
        vol[i] += amount_cents / 100.0
        cnt[i] += count
    for i in range(1, n):
        vol[i] += vol[i - 1]
        cnt[i] += cnt[i - 1]
    total = vol[-1]
    baseline = [total * i / (n - 1) for i in range(n)] if n > 1 else [0.0]
    return {"t": t, "volume": vol, "txns": cnt, "baseline": baseline}
