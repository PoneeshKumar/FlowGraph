"""
Blindspot analyzer for the cycle detection engine.

Takes the set of missed CycleGroups (labeled but not detected) and attributes
each miss to the first filter in the detection pipeline that would have excluded it.

Filter order matches the Cypher/Python pipeline:
  1. OUTSIDE_WINDOW   — any edge timestamp outside the 48h look-back window
  2. HOP_GAP          — any consecutive hop gap > MAX_HOP_GAP_HOURS
  3. AMOUNT_FLOOR     — weakest hop below CYCLE_MIN_VALUE_CENTS ($1k default)
  4. CONSERVATION     — any hop amount grows, or bleeds off > MAX_LEAK (20%)
  5. CROSS_CURRENCY   — cycle crosses currency boundaries (FX mismatch; amounts not comparable)
  6. DEPTH_LIMIT      — cycle hop count > CYCLE_MAX_DEPTH (6)
  7. UNKNOWN          — none of the above (graph write failure or genuine algorithm gap)

Usage:
    from benchmarks.ibm_aml.blindspots import analyze_misses, BlindspotReport
    report = analyze_misses(missed_groups, now_epoch=int(time.time()))
    print(report.summary())
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from benchmarks.ibm_aml.patterns import CycleGroup, _parse_ts
from config import (
    CYCLE_WINDOW_HOURS,
    CYCLE_MAX_HOP_GAP_HOURS,
    CYCLE_MIN_VALUE_CENTS,
    CYCLE_MAX_LEAK,
    CYCLE_MAX_DEPTH,
)

logger = logging.getLogger(__name__)


MISS_CAUSES = [
    "OUTSIDE_WINDOW",
    "HOP_GAP",
    "AMOUNT_FLOOR",
    "CONSERVATION",
    "CROSS_CURRENCY",
    "DEPTH_LIMIT",
    "UNKNOWN",
]


@dataclass
class MissedGroup:
    group_id: int
    n_hops: int
    min_amount_cents: int
    span_seconds: int
    is_cross_currency: bool
    currencies: list[str]
    cause: str                 # one of MISS_CAUSES


@dataclass
class BlindspotReport:
    total_labeled: int
    true_positives: int
    false_negatives: int
    false_positives: int
    miss_breakdown: dict[str, int]       # cause → count
    missed_groups: list[MissedGroup]
    precision: float
    recall: float
    f1: float
    window_hours: int = CYCLE_WINDOW_HOURS
    max_hop_gap_hours: float = CYCLE_MAX_HOP_GAP_HOURS
    min_value_cents: int = CYCLE_MIN_VALUE_CENTS
    max_leak: float = CYCLE_MAX_LEAK
    max_depth: int | None = CYCLE_MAX_DEPTH

    def summary(self) -> str:
        lines = [
            "",
            "=" * 60,
            "  Cycle Detector Blindspot Report (IBM AML HI-Small)",
            "=" * 60,
            f"  Total labeled CYCLE groups : {self.total_labeled}",
            f"  Detected (TP)              : {self.true_positives}"
            f"  ({self.recall*100:.1f}% recall)",
            f"  Missed  (FN)               : {self.false_negatives}",
        ]
        for cause in MISS_CAUSES:
            count = self.miss_breakdown.get(cause, 0)
            if count:
                label = {
                    "OUTSIDE_WINDOW": f"loops span > {self.window_hours}h window",
                    "HOP_GAP":        f"consecutive hop gap > {self.max_hop_gap_hours}h",
                    "AMOUNT_FLOOR":   f"min hop < ${self.min_value_cents/100:,.0f}",
                    "CONSERVATION":   f">{ int(self.max_leak*100)}% bleed or amount grew",
                    "CROSS_CURRENCY": "cross-currency (FX mismatch — amounts not comparable)",
                    "DEPTH_LIMIT":    f"hop count > {self.max_depth}",
                    "UNKNOWN":        "unattributed — check graph writes or algorithm",
                }[cause]
                lines.append(f"    {cause:<18}: {count:>4}  ({label})")
        lines += [
            f"  False positives (FP)       : {self.false_positives}",
            f"  Precision : {self.precision*100:.1f}%",
            f"  Recall    : {self.recall*100:.1f}%",
            f"  F1        : {self.f1*100:.1f}%",
            "=" * 60,
            "",
        ]
        return "\n".join(lines)

    def to_json(self) -> str:
        d = asdict(self)
        # set is not JSON-serializable — already converted to list by asdict
        return json.dumps(d, indent=2, default=str)


def _attribute_miss(group: CycleGroup, now_epoch: int) -> str:
    """
    Attribute a missed cycle to the first detection-pipeline filter it fails.

    We simulate the filter logic from find_cycles and the Python post-processing
    using only the metadata already in CycleGroup (no live Neo4j call needed).
    """
    window_start = datetime.fromtimestamp(now_epoch, tz=timezone.utc) - timedelta(
        hours=CYCLE_WINDOW_HOURS
    )
    max_hop_gap = timedelta(hours=CYCLE_MAX_HOP_GAP_HOURS)

    # Reconstruct ordered timestamps from raw_rows (best effort). Kept as
    # datetime objects (not raw floats) end to end — comparisons below read
    # as "is this hop older than the window" / "is this gap too wide", not
    # arithmetic on opaque epoch numbers.
    timestamps: list[datetime] = []
    amounts: list[int] = []
    currencies: list[str] = []

    for row in group.raw_rows:
        ts = _parse_ts(row.get("Timestamp", ""))
        if ts:
            timestamps.append(ts)
        try:
            amounts.append(round(float(row.get("Amount Paid", "0")) * 100))
        except ValueError:
            logger.debug(f"Failed to parse amount from row: {row}")
        currencies.append(row.get("Payment Currency", "").strip())

    timestamps.sort()

    # 1. OUTSIDE_WINDOW — any edge older than the window
    if timestamps and min(timestamps) < window_start:
        return "OUTSIDE_WINDOW"

    # 2. HOP_GAP — any consecutive pair more than MAX_HOP_GAP apart. A cycle
    # group is bounded by CYCLE_MAX_DEPTH (a handful of hops), so this is a
    # few comparisons — not the kind of scale a DataFrame/array conversion
    # would pay off on; zip-over-pairs is the plain, allocation-free version.
    for prev_ts, curr_ts in zip(timestamps, timestamps[1:]):
        if curr_ts - prev_ts > max_hop_gap:
            return "HOP_GAP"

    # 3. AMOUNT_FLOOR — weakest hop below the floor
    if amounts and min(amounts) < CYCLE_MIN_VALUE_CENTS:
        return "AMOUNT_FLOOR"

    # 4. CONSERVATION — any hop's amount grows OR bleeds >MAX_LEAK
    # (compare sorted amounts as a proxy; raw ordering not available here)
    if len(amounts) >= 2:
        sorted_amts = sorted(amounts, reverse=True)
        for i in range(len(sorted_amts) - 1):
            prev, curr = sorted_amts[i], sorted_amts[i + 1]
            if prev == 0:
                continue
            if curr > prev:
                return "CONSERVATION"
            if curr < prev * (1.0 - CYCLE_MAX_LEAK):
                return "CONSERVATION"

    # 5. CROSS_CURRENCY — cycle uses multiple currencies
    if group.is_cross_currency:
        return "CROSS_CURRENCY"

    # 6. DEPTH_LIMIT — too many hops
    if group.n_hops > CYCLE_MAX_DEPTH:
        return "DEPTH_LIMIT"

    # 7. UNKNOWN
    return "UNKNOWN"


def analyze_misses(
    missed_groups: list[CycleGroup],
    true_positives: int,
    false_positives: int,
    total_labeled: int,
    now_epoch: Optional[int] = None,
) -> BlindspotReport:
    """
    Attribute each missed cycle to a cause and produce a BlindspotReport.

    Args:
        missed_groups:   CycleGroups that were labeled but not detected
        true_positives:  Count of correctly detected groups
        false_positives: Count of flags raised against non-labeled accounts
        total_labeled:   Total labeled CYCLE groups in the dataset
        now_epoch:       Unix timestamp to use as "now" (defaults to current time)

    Returns:
        BlindspotReport with per-cause counts and overall metrics
    """
    if now_epoch is None:
        now_epoch = int(datetime.now(timezone.utc).timestamp())

    breakdown: dict[str, int] = {c: 0 for c in MISS_CAUSES}
    detailed: list[MissedGroup] = []

    for group in missed_groups:
        cause = _attribute_miss(group, now_epoch)
        breakdown[cause] += 1
        detailed.append(
            MissedGroup(
                group_id=group.group_id,
                n_hops=group.n_hops,
                min_amount_cents=group.min_amount_cents,
                span_seconds=group.span_seconds,
                is_cross_currency=group.is_cross_currency,
                currencies=sorted(group.currencies),
                cause=cause,
            )
        )

    fn = len(missed_groups)
    tp = true_positives
    fp = false_positives

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)

    return BlindspotReport(
        total_labeled=total_labeled,
        true_positives=tp,
        false_negatives=fn,
        false_positives=fp,
        miss_breakdown=breakdown,
        missed_groups=detailed,
        precision=precision,
        recall=recall,
        f1=f1,
    )
