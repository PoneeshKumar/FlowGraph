"""
IBM AML benchmark runner for Louvain community detection.

Validates the community detector against the labeled NON-cycle typologies
(gather-scatter, scatter-gather, bipartite, stack, random, fan-in, fan-out) —
the structures cycle detection is blind to by design.

Pipeline:
  1. Parse labeled groups for the target typologies from the patterns file
  2. Ingest their transactions + background sample into Neo4j (reuses the
     production writer via benchmarks.ibm_aml.ingestor.ingest)
  3. Run CommunityDetector.run() once, anchored to the dataset's max timestamp
  4. Recall:    a group counts as detected when a single flagged community
                contains >= --containment (default 0.5) of the group's accounts
  5. Precision: a flagged community counts as a true positive when
                >= --precision-overlap (default 0.25) of its members are
                labeled laundering accounts (ANY typology, cycles included)
  6. Print report + write JSON to benchmarks/results/

Usage:
    LOUVAIN_WINDOW_DAYS=60 python -m benchmarks.ibm_aml.louvain_runner \\
        --csv      benchmarks/data/HI-Small_Trans.csv \\
        --patterns benchmarks/data/HI-Small_Patterns.txt \\
        --background-ratio 5.0 \\
        --report   benchmarks/results/louvain_$(date +%Y%m%d).json

Notes:
  - LOUVAIN_WINDOW_DAYS must cover the ~30-day dataset span; 60 is safe.
  - Without --with-postgres the overlap dimension is 0 for every community
    (max achievable composite 0.65). Run the cycle benchmark first WITH
    postgres, then pass --with-postgres here, to measure the cross-detector
    corroboration lift.
  - Use --skip-ingest on re-runs against an already-loaded Neo4j.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from benchmarks.ibm_aml.ingestor import ingest
from benchmarks.ibm_aml.patterns import _parse_ts, load_pattern_groups
from db.neo4j import Neo4jClient
from db.postgres import PostgresClient
from fraud.community_detector import CommunityDetector

logger = logging.getLogger(__name__)

ALL_TYPOLOGIES = [
    "CYCLE", "FAN-IN", "FAN-OUT", "GATHER-SCATTER",
    "SCATTER-GATHER", "BIPARTITE", "STACK", "RANDOM",
]
DEFAULT_TARGETS = "GATHER-SCATTER,SCATTER-GATHER,BIPARTITE,STACK,RANDOM,FAN-IN,FAN-OUT"


def _dataset_max_ts(groups) -> datetime:
    """Anchor detection to the dataset's own clock, not wall time."""
    max_ts = None
    for g in groups:
        for row in g.raw_rows:
            ts = _parse_ts(row.get("Timestamp", ""))
            if ts and (max_ts is None or ts > max_ts):
                max_ts = ts
    if max_ts is None:
        raise SystemExit("No parseable timestamps in loaded groups — cannot anchor window")
    return max_ts


def score_recall(groups, flags, containment: float):
    """Per-typology TP/FN. A group is detected when one flagged community
    contains >= containment of the group's accounts."""
    flag_member_sets = [set(f["account_ids"]) for f in flags]
    per_typology: dict = {}
    for g in groups:
        bucket = per_typology.setdefault(g.typology, {"tp": 0, "fn": 0, "groups": 0})
        bucket["groups"] += 1
        accounts = set(g.accounts)
        best = max(
            (len(accounts & members) / len(accounts) for members in flag_member_sets),
            default=0.0,
        )
        if best >= containment:
            bucket["tp"] += 1
        else:
            bucket["fn"] += 1
    return per_typology


def score_precision(flags, labeled_accounts: set, min_overlap: float):
    """Fraction of flagged communities that substantially overlap labeled
    laundering accounts of ANY typology."""
    if not flags:
        return {"tp": 0, "fp": 0, "precision": None}
    tp = sum(
        1 for f in flags
        if len(set(f["account_ids"]) & labeled_accounts) / len(f["account_ids"]) >= min_overlap
    )
    fp = len(flags) - tp
    return {"tp": tp, "fp": fp, "precision": tp / len(flags)}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--patterns", required=True)
    parser.add_argument("--typologies", default=DEFAULT_TARGETS,
                        help="Comma-separated recall targets")
    parser.add_argument("--background-ratio", type=float, default=5.0)
    parser.add_argument("--containment", type=float, default=0.5,
                        help="Group-account fraction one community must contain")
    parser.add_argument("--precision-overlap", type=float, default=0.25,
                        help="Labeled-member fraction for a flag to count as TP")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--with-postgres", action="store_true",
                        help="Persist flags + use cross-detector overlap scoring")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    targets = [t.strip().upper() for t in args.typologies.split(",") if t.strip()]
    target_groups = load_pattern_groups(args.patterns, targets)
    all_groups = load_pattern_groups(args.patterns, ALL_TYPOLOGIES)
    labeled_accounts = {a for g in all_groups for a in g.accounts}
    logger.info("Loaded %d target groups (%s); %d labeled accounts overall",
                len(target_groups), ",".join(targets), len(labeled_accounts))
    if not target_groups:
        raise SystemExit("No labeled groups for the requested typologies")

    neo4j = Neo4jClient()
    await neo4j.initialize()
    await neo4j.init_constraints()
    postgres = None
    if args.with_postgres:
        postgres = PostgresClient()
        await postgres.initialize()

    if not args.skip_ingest:
        stats = await ingest(
            args.csv, target_groups, neo4j_client=neo4j,
            background_ratio=args.background_ratio,
        )
        logger.info("Ingest done: %s", stats)

    reference_time = _dataset_max_ts(target_groups)
    logger.info("Detection anchored to dataset max timestamp: %s", reference_time)

    t0 = time.monotonic()
    detector = CommunityDetector(neo4j, postgres)
    result = await detector.run(reference_time=reference_time)
    runtime_s = time.monotonic() - t0

    recall = score_recall(target_groups, result["flags"], args.containment)
    total_tp = sum(b["tp"] for b in recall.values())
    total_groups = sum(b["groups"] for b in recall.values())
    precision = score_precision(result["flags"], labeled_accounts, args.precision_overlap)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(runtime_s, 2),
        "config": {
            "typologies": targets,
            "containment": args.containment,
            "precision_overlap": args.precision_overlap,
            "with_postgres": args.with_postgres,
        },
        "communities_kept": result["communities"],
        "flags": len(result["flags"]),
        "recall_overall": round(total_tp / total_groups, 4) if total_groups else None,
        "recall_by_typology": {
            t: {**b, "recall": round(b["tp"] / b["groups"], 4)}
            for t, b in sorted(recall.items())
        },
        "precision": precision,
    }

    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2))
        logger.info("Report written to %s", args.report)

    await neo4j.close()
    if postgres:
        await postgres.close()


if __name__ == "__main__":
    asyncio.run(main())
