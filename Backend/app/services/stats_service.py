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


_HIST_Q = (
    "MATCH ()-[t:TRANSFER]->() "
    "WITH t.rail AS rail, toInteger(t.ts / 1800) AS b, t.amount_cents AS amt "
    "RETURN rail, b, count(*) AS n, sum(amt) AS total"
)
_COUNT_QS = {
    "accounts": "MATCH (a:Account) RETURN count(a) AS n",
    "flows": "MATCH ()-[r:FLOWS_TO]->() RETURN count(r) AS n",
    "transactions": "MATCH ()-[t:TRANSFER]->() RETURN count(t) AS n",
    "scored_accounts": "MATCH (a:Account) WHERE a.gnn_risk_score IS NOT NULL RETURN count(a) AS n",
    "communities": "MATCH (a:Account) WHERE a.community_id IS NOT NULL RETURN count(DISTINCT a.community_id) AS n",
    "in_cycle": "MATCH (a:Account) WHERE a.in_cycle = true RETURN count(a) AS n",
}


class StatsCache:
    """Whole-graph numbers, computed once. `build` is the only expensive call
    (one ~8 s TRANSFER scan on the IBM graph); everything else is in-memory."""

    def __init__(self) -> None:
        self._hist: Dict[str, Dict[int, Tuple[int, int]]] = {}   # rail → base histogram
        self._counts: Dict[str, int] = {}
        self._open_flags: Dict[str, int] = {}
        self._flagged: Set[str] = set()
        self._dataset: Dict[str, Any] = {}
        self._latest_run: Optional[Dict[str, Any]] = None
        self._computed_at: Optional[int] = None
        self._lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------------
    def ready(self) -> bool:
        return self._computed_at is not None

    def invalidate(self) -> None:
        self._computed_at = None

    async def build(self, session: Callable[[], Any], pg: Any) -> None:
        async with self._lock:
            hist: Dict[str, Dict[int, Tuple[int, int]]] = {}
            counts: Dict[str, int] = {}
            async with session() as s:
                res = await s.run(_HIST_Q)
                async for r in res:
                    rail = r["rail"] or "UNKNOWN"
                    hist.setdefault(rail, {})[int(r["b"])] = (int(r["n"]), int(r["total"] or 0))
                for key, q in _COUNT_QS.items():
                    rec = await (await s.run(q)).single()
                    counts[key] = int(rec["n"]) if rec else 0
            dataset = await pg.get_app_meta("dataset") or {}
            if "start_ts" not in dataset or "end_ts" not in dataset:
                bs = [b for h in hist.values() for b in h]
                dataset = dict(dataset)
                dataset.setdefault("name", "loaded graph")
                dataset.setdefault("source", "unknown")
                dataset.setdefault("labelled", False)
                dataset["start_ts"] = min(bs) * BUCKET_BASE if bs else 0
                dataset["end_ts"] = (max(bs) + 1) * BUCKET_BASE if bs else 0
            open_flags = await pg.count_open_flags_by_type()
            flagged = set(await pg.get_account_ids_for_flag_type("AGGREGATE", "open"))
            latest = await pg.get_latest_pipeline_run()
            self._hist, self._counts, self._dataset = hist, counts, dataset
            self._open_flags, self._flagged, self._latest_run = open_flags, flagged, latest
            self._computed_at = int(time.time())
            logger.info("stats cache built: %d rails, %d accounts", len(hist), counts.get("accounts", 0))

    # -- reads ------------------------------------------------------------------
    def _currencies(self) -> List[Dict[str, Any]]:
        out = []
        for rail, h in self._hist.items():
            n = sum(c for c, _ in h.values())
            amt = sum(a for _, a in h.values())
            out.append({"code": currency_code(rail), "rail": rail, "tx_count": n, "volume": amt / 100.0})
        out.sort(key=lambda x: x["tx_count"], reverse=True)
        return out

    def rail_for(self, code: str) -> Optional[str]:
        for rail in self._hist:
            if currency_code(rail) == code or rail == code:
                return rail
        return None

    def overview(self) -> Dict[str, Any]:
        flags = dict(self._open_flags)
        flags["total"] = sum(self._open_flags.values())
        return {
            "dataset": self._dataset,
            **self._counts,
            "open_flags": flags,
            "currencies": self._currencies(),
            "latest_run": self._latest_run,
            "computed_at": self._computed_at,
        }

    def series(self, currency: str, period: str) -> Dict[str, Any]:
        rail = self.rail_for(currency)
        if rail is None:
            raise KeyError(currency)
        start, end, bucket = period_window(period, int(self._dataset["start_ts"]), int(self._dataset["end_ts"]))
        out = build_series(self._hist[rail], start, end, bucket)
        out.update({"currency": currency_code(rail), "period": period, "anchor_ts": end,
                    "start_ts": start, "bucket_seconds": bucket})
        return out

    def is_flagged(self, account_id: str) -> bool:
        return account_id in self._flagged

    def dataset_end_ts(self) -> Optional[int]:
        if not self.ready():
            return None
        return int(self._dataset["end_ts"])


cache = StatsCache()


def invalidate() -> None:
    cache.invalidate()


async def warmup(session: Callable[[], Any], pg: Any) -> None:
    """Build the cache in the background at startup. Never raises — a failure
    leaves the cache not-ready and the endpoints answer 503 until a retry."""
    if pg is None:
        return
    try:
        await cache.build(session, pg)
    except Exception:  # noqa: BLE001 — logged, endpoints report warming
        logger.exception("stats cache warm-up failed")
