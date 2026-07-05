"""
IBM AML benchmark runner.

Orchestrates the full validation pipeline:
  1. Parse labeled CYCLE groups from patterns file (+ CSV fallback)
  2. Ingest cycle transactions + background sample into Neo4j
  3. Run CycleDetector.detect() for each account in each labeled group
  4. Compare detected flags against labels → TP / FP / FN
  5. Run blindspot analysis on missed groups
  6. Print report + write JSON output file

Usage:
    python -m benchmarks.ibm_aml.runner \\
        --csv      benchmarks/data/HI-Small_Trans.csv \\
        --patterns benchmarks/data/HI-Small_Patterns.txt \\
        --background-ratio 5.0 \\
        --neo4j-only \\
        --report   benchmarks/results/ibm_aml_$(date +%Y%m%d).json

Pre-requisites:
    1. docker compose up  (Neo4j running on localhost:7687)
    2. Download HI-Small_Trans.csv + HI-Small_Patterns.txt from Kaggle
       https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml
       and place in benchmarks/data/
    3. pip install -r requirements.txt

Tuned results (54 labeled CYCLE groups, 10.2k cycle + 51k background txns).
The window MUST be wide enough to cover the ~30-day dataset, and detection is anchored
to the dataset's own max timestamp (not now()), so pass CYCLE_WINDOW_HOURS=720.

    depth  gap   conservation  recall  precision  F1     runtime   use
    ─────  ────  ────────────  ──────  ─────────  ─────  ───────   ─────────────────
    8      168h  hop           72.2%   100.0%     83.9   ~24s      real-time / streaming
    12     168h  hop           87.0%   100.0%     93.1   ~2-15min  batch / investigative

Best real-time config:
    CYCLE_MAX_DEPTH=8 CYCLE_WINDOW_HOURS=720 CYCLE_MAX_HOP_GAP_HOURS=168 \\
    CYCLE_CONSERVATION_MODE=hop \\
    python -m benchmarks.ibm_aml.runner --csv ... --patterns ... --neo4j-only --skip-ingest

Best coverage config (batch): same, with CYCLE_MAX_DEPTH=12.

The ~13% still missed at depth 12 are two structural blindspots that are NOT cycle-detection
tuning knobs, by design:
  - cross-currency cycles: conservation compares raw cents, but FX makes cents across
    currencies incomparable (Yuan cents ~7x USD for equal value). Fixing needs FX rates,
    which the dataset does not carry.
  - >12-hop chains: these are community/layering structures, the job of the Louvain and
    PageRank detectors, not tight-cycle detection.

On the 100% precision figure: it is measured against realistic noise, not isolated rings.
Cycle accounts carry ~45% legitimate edges — in the ingested graph the 271 labeled accounts
have ~569 within-ring edges AND ~474 edges to outside (non-laundering) accounts — so the
detector must avoid stitching false rings through genuine neighbourhood traffic, and does.
The disjoint background sample adds scale but cannot form rings through labeled accounts.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Ensure Backend/ is on sys.path when run as __main__
_BACKEND = Path(__file__).parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from benchmarks.ibm_aml.ingestor import ingest, IngestStats
from benchmarks.ibm_aml.patterns import load_cycle_groups, CycleGroup, _parse_ts, _TS_FORMATS
from benchmarks.ibm_aml.blindspots import analyze_misses, BlindspotReport
from config import CYCLE_WINDOW_HOURS
from db.neo4j import Neo4jClient
from db.postgres import PostgresClient
from fraud.cycle_detector import CycleDetector

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    ingest_stats: IngestStats
    total_labeled_cycle_groups: int
    true_positives: int
    false_negatives: int
    false_positives: int
    missed_groups: list[CycleGroup]
    detected_fingerprints: set[str]
    blindspot_report: BlindspotReport
    elapsed_seconds: float


async def run_benchmark(
    csv_path: str,
    patterns_path: str | None,
    neo4j_client: Neo4jClient,
    postgres_client: PostgresClient | None,
    background_ratio: float = 5.0,
    skip_ingest: bool = False,
) -> BenchmarkResult:
    """
    Full benchmark pipeline.

    Returns a BenchmarkResult with all metrics and the blindspot report.
    """
    t0 = time.monotonic()

    # ------------------------------------------------------------------ #
    # Step 1 — Load labeled CYCLE groups
    # ------------------------------------------------------------------ #
    logger.info("Loading labeled CYCLE groups …")
    cycle_groups = load_cycle_groups(patterns_path, csv_path)

    if not cycle_groups:
        logger.warning(
            "No labeled CYCLE groups found. "
            "Check that HI-Small_Patterns.txt is present and non-empty. "
            "Falling back to CSV-derived groups — these will have no ground-truth typology label."
        )

    logger.info("Loaded %d labeled CYCLE groups", len(cycle_groups))

    # Anchor the detection window to the dataset's own time period, not now.
    # The IBM AML data is from September 2022; using datetime.now() would place
    # every edge outside any lookback window. We scan the full CSV for the max
    # timestamp so the window covers the entire dataset, not just the cycle groups.
    logger.info("Scanning CSV for max timestamp to anchor detection window …")
    # Only the first column is needed — read positionally (usecols=[0]) so the
    # duplicate "Account" header downstream never comes into play. Vectorized
    # pd.to_datetime against the dataset's primary format is far faster than a
    # per-row csv.reader loop over a multi-million-row file.
    ts_column = pd.read_csv(
        csv_path, usecols=[0], header=0, names=["Timestamp"]
    )["Timestamp"]
    parsed = pd.to_datetime(ts_column, format=_TS_FORMATS[0], errors="coerce", utc=True)

    # Fall back to the multi-format parser for any rows the primary format
    # didn't match — rare, kept only for robustness against format drift.
    unparsed = parsed.isna()
    if unparsed.any():
        parsed.loc[unparsed] = pd.to_datetime(
            ts_column[unparsed].map(_parse_ts), utc=True
        )

    dataset_reference_time: datetime | None = (
        parsed.max().to_pydatetime() if parsed.notna().any() else None
    )

    if dataset_reference_time:
        dataset_reference_time = dataset_reference_time + timedelta(hours=1)
        logger.info(
            "Dataset reference time: %s  (window looks back %dh from here)",
            dataset_reference_time.isoformat(),
            CYCLE_WINDOW_HOURS,
        )

    # ------------------------------------------------------------------ #
    # Step 2 — Ingest into Neo4j (skippable when graph is already populated)
    # ------------------------------------------------------------------ #
    await neo4j_client.init_constraints()

    if skip_ingest:
        logger.info("Skipping ingest (--skip-ingest flag set — graph assumed populated)")
        stats = IngestStats()
    else:
        logger.info("Ingesting transactions into Neo4j …")
        stats = await ingest(
            csv_path=csv_path,
            cycle_groups=cycle_groups,
            neo4j_client=neo4j_client,
            postgres_client=postgres_client,
            background_ratio=background_ratio,
        )

    # ------------------------------------------------------------------ #
    # Step 3 — Run CycleDetector for each labeled group's accounts
    # ------------------------------------------------------------------ #
    detector = CycleDetector(neo4j_client, postgres_client)
    detected_fingerprints: set[str] = set()
    # Collect every detected flag (with its ring account_ids) in memory. This is the
    # source of truth for matching — it works in --neo4j-only mode too, where nothing
    # is persisted to Postgres.
    all_flags: list[dict] = []

    # Match predicate (also used for the per-group early exit below).
    def _matches_group(flag_accounts: list[str], group: CycleGroup) -> bool:
        flag_set = frozenset(flag_accounts)
        if not flag_set:
            return False
        overlap = len(flag_set & group.account_set)
        return overlap >= 2 and overlap >= 0.6 * len(flag_set)

    logger.info("Running cycle detection over %d labeled groups …", len(cycle_groups))
    for i, group in enumerate(cycle_groups):
        for account_id in group.accounts:
            try:
                flags = await detector.detect(
                    account_id, reference_time=dataset_reference_time
                )
            except Exception as e:
                logger.warning(
                    "detect() failed for account %s in group %d: %s",
                    account_id[:8], group.group_id, e,
                )
                continue
            group_hit = False
            for flag in flags:
                if flag["fingerprint"] not in detected_fingerprints:
                    detected_fingerprints.add(flag["fingerprint"])
                    all_flags.append(flag)
                if _matches_group(flag.get("account_ids", []), group):
                    group_hit = True
            # The same ring returns from every node on it — once this group is found,
            # seeding detection from its remaining accounts is wasted work (a big saving
            # at high depth). Background/false-positive rings are still surfaced because
            # every account is seeded until its own group is hit.
            if group_hit:
                break
        if (i + 1) % 10 == 0:
            logger.info(
                "  detection progress: %d/%d groups | flags so far: %d",
                i + 1, len(cycle_groups), len(detected_fingerprints),
            )

    # ------------------------------------------------------------------ #
    # Step 4 — Match detected flags to labeled groups
    # ------------------------------------------------------------------ #
    # A detected ring is a subset of some labeled group's accounts (see _matches_group
    # above): it must share >= 2 accounts and >= 60% of the ring's own accounts must fall
    # inside the group, so a large ring can't coincidentally match a small group.
    matched_group_ids: set[int] = set()
    matched_flag_idx: set[int] = set()
    for gi, group in enumerate(cycle_groups):
        for fi, f in enumerate(all_flags):
            if _matches_group(f.get("account_ids", []), group):
                matched_group_ids.add(group.group_id)
                matched_flag_idx.add(fi)

    # A flag that matches no labeled group is a false positive.
    fp_count = sum(1 for fi in range(len(all_flags)) if fi not in matched_flag_idx)

    tp = len(matched_group_ids)
    missed_groups = [g for g in cycle_groups if g.group_id not in matched_group_ids]
    fn = len(missed_groups)

    # ------------------------------------------------------------------ #
    # Step 5 — Blindspot analysis
    # ------------------------------------------------------------------ #
    now_epoch = int(
        dataset_reference_time.timestamp()
        if dataset_reference_time
        else datetime.now(timezone.utc).timestamp()
    )
    blindspot_report = analyze_misses(
        missed_groups=missed_groups,
        true_positives=tp,
        false_positives=fp_count,
        total_labeled=len(cycle_groups),
        now_epoch=now_epoch,
    )

    elapsed = time.monotonic() - t0

    return BenchmarkResult(
        ingest_stats=stats,
        total_labeled_cycle_groups=len(cycle_groups),
        true_positives=tp,
        false_negatives=fn,
        false_positives=fp_count,
        missed_groups=missed_groups,
        detected_fingerprints=detected_fingerprints,
        blindspot_report=blindspot_report,
        elapsed_seconds=elapsed,
    )


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="IBM AML cycle-detection benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--csv",
        default="benchmarks/data/HI-Small_Trans.csv",
        help="Path to HI-Small_Trans.csv",
    )
    p.add_argument(
        "--patterns",
        default="benchmarks/data/HI-Small_Patterns.txt",
        help="Path to HI-Small_Patterns.txt (optional — falls back to CSV SCC derivation)",
    )
    p.add_argument(
        "--background-ratio",
        type=float,
        default=5.0,
        help="Background sample size = cycle_txn_count × this ratio",
    )
    p.add_argument(
        "--neo4j-only",
        action="store_true",
        help="Skip Postgres writes (faster — Neo4j only)",
    )
    p.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip CSV ingest — assume graph is already populated (for parallel detection runs)",
    )
    p.add_argument(
        "--report",
        default="",
        help="Path to write JSON report (default: benchmarks/results/ibm_aml_<timestamp>.json)",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    # Initialise clients
    neo4j_client = Neo4jClient()
    await neo4j_client.initialize()

    postgres_client: PostgresClient | None = None
    if not args.neo4j_only:
        postgres_client = PostgresClient()
        await postgres_client.initialize()
        # Run the risk_flags migration so the table exists
        migration_sql = (
            _BACKEND / "migrations" / "002_create_risk_flags_table.sql"
        ).read_text()
        async with postgres_client._get_connection() as conn:
            await conn.execute(migration_sql)

    try:
        result = await run_benchmark(
            csv_path=args.csv,
            patterns_path=args.patterns if Path(args.patterns).exists() else None,
            neo4j_client=neo4j_client,
            postgres_client=postgres_client,
            background_ratio=args.background_ratio,
            skip_ingest=args.skip_ingest,
        )
    finally:
        await neo4j_client.close()
        if postgres_client:
            await postgres_client.close()

    # Print summary
    print(result.blindspot_report.summary())
    print(f"  Elapsed: {result.elapsed_seconds:.1f}s")
    print(
        f"  Ingest : {result.ingest_stats.cycle_txns_written} cycle txns + "
        f"{result.ingest_stats.background_written} background txns"
    )

    # Write JSON report
    report_path = args.report
    if not report_path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        results_dir = _BACKEND / "benchmarks" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        report_path = str(results_dir / f"ibm_aml_{ts}.json")

    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(result.blindspot_report.to_json())
    print(f"  Report : {report_path}")


if __name__ == "__main__":
    asyncio.run(_main())
