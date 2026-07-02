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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Ensure Backend/ is on sys.path when run as __main__
_BACKEND = Path(__file__).parent.parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from benchmarks.ibm_aml.ingestor import ingest, IngestStats
from benchmarks.ibm_aml.patterns import load_cycle_groups, CycleGroup
from benchmarks.ibm_aml.blindspots import analyze_misses, BlindspotReport
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

    # ------------------------------------------------------------------ #
    # Step 2 — Ingest into Neo4j
    # ------------------------------------------------------------------ #
    logger.info("Ingesting transactions into Neo4j …")
    await neo4j_client.init_constraints()

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

    logger.info("Running cycle detection over %d labeled groups …", len(cycle_groups))
    for i, group in enumerate(cycle_groups):
        for account_id in group.accounts:
            try:
                flags = await detector.detect(account_id)
                for flag in flags:
                    detected_fingerprints.add(flag["fingerprint"])
            except Exception as e:
                logger.warning(
                    "detect() failed for account %s in group %d: %s",
                    account_id[:8], group.group_id, e,
                )
        if (i + 1) % 10 == 0:
            logger.info(
                "  detection progress: %d/%d groups | flags so far: %d",
                i + 1, len(cycle_groups), len(detected_fingerprints),
            )

    # Also run detection over background accounts to surface false positives.
    # We detect ALL flagged fingerprints from the detector's risk_flags store.
    # We consider a detection a false positive if its account_ids have no overlap
    # with any labeled CYCLE group's accounts.
    labeled_account_sets = [g.account_set for g in cycle_groups]

    def _is_false_positive(flag_account_ids: list[str]) -> bool:
        flag_set = frozenset(flag_account_ids)
        for labeled_set in labeled_account_sets:
            if len(flag_set & labeled_set) >= max(1, len(labeled_set) - 1):
                return False  # overlaps a labeled group — true positive
        return True

    # ------------------------------------------------------------------ #
    # Step 4 — Match detected fingerprints to labeled groups
    # ------------------------------------------------------------------ #
    # Query the persisted risk_flags to get account_ids for each fingerprint
    all_flags: list[dict] = []
    if postgres_client:
        try:
            all_flags = await postgres_client.get_risk_flags(flag_type="CYCLE", limit=10_000)
        except Exception as e:
            logger.warning("Could not query risk_flags: %s", e)

    fp_count = sum(
        1 for f in all_flags
        if _is_false_positive(f.get("account_ids", []))
    )

    # A labeled CYCLE group is a TP if any of its accounts produced a detected flag
    # whose account_ids overlap with the group's accounts.
    matched_group_ids: set[int] = set()
    for f in all_flags:
        flag_set = frozenset(f.get("account_ids", []))
        for group in cycle_groups:
            if group.group_id not in matched_group_ids:
                if len(flag_set & group.account_set) >= max(1, len(group.account_set) - 1):
                    matched_group_ids.add(group.group_id)

    tp = len(matched_group_ids)
    missed_groups = [g for g in cycle_groups if g.group_id not in matched_group_ids]
    fn = len(missed_groups)

    # ------------------------------------------------------------------ #
    # Step 5 — Blindspot analysis
    # ------------------------------------------------------------------ #
    now_epoch = int(datetime.now(timezone.utc).timestamp())
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
