"""
CLI for loading an IBM AML dataset into Neo4j + Redis for GNN training.

    # smoke test — first 100k rows, no PageRank
    python3 -m ml.datasets.run_ingest --row-limit 100000 --no-pagerank

    # full load, all 8 typologies, every background row
    python3 -m ml.datasets.run_ingest --max-background none

    # a different variant
    python3 -m ml.datasets.run_ingest \
        --csv benchmarks/data/HI-Medium_Trans.csv \
        --patterns benchmarks/data/HI-Medium_Patterns.txt

Requires neo4j and redis to be up (docker compose up -d neo4j redis postgres).

Constraints are created before loading. That is not optional: MERGE on
(:Account {id}) without the backing index does a full node scan per row, which
turns a minutes-long load into an overnight one.
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from ml.datasets.ibm_aml import ALL_TYPOLOGIES, ingest_for_training

logger = logging.getLogger("run_ingest")

DEFAULT_CSV = "benchmarks/data/HI-Small_Trans.csv"
DEFAULT_PATTERNS = "benchmarks/data/HI-Small_Patterns.txt"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--patterns", default=DEFAULT_PATTERNS)
    parser.add_argument(
        "--typologies",
        nargs="*",
        default=list(ALL_TYPOLOGIES),
        help="laundering typologies to treat as labelled patterns",
    )
    parser.add_argument(
        "--max-background",
        default="2000000",
        help="cap on non-pattern rows, or 'none' for no cap",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--row-limit", type=int, default=None, help="stop after N CSV rows"
    )
    parser.add_argument(
        "--no-redis",
        action="store_true",
        help="skip Redis (leaves 12 of the 29 features at zero)",
    )
    parser.add_argument(
        "--no-pagerank",
        action="store_true",
        help="skip the full-graph PageRank pass at the end",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="DELETE the existing graph and flush Redis before loading",
    )
    parser.add_argument(
        "--pagerank-only",
        action="store_true",
        help="skip ingestion; only run the full-graph PageRank pass over the "
             "graph already loaded (use after an ingest whose PageRank step failed)",
    )
    parser.add_argument(
        "--export-timeout",
        type=float,
        default=1800.0,
        help="seconds allowed for the FLOWS_TO edge export during PageRank",
    )
    return parser.parse_args()


async def _reset_stores(neo4j_client, redis_client) -> None:
    """Wipe the graph so a reload does not inflate FLOWS_TO aggregates.

    FLOWS_TO aggregates are incremented on MATCH, so loading the same rows twice
    doubles tx_count and total_amount. Re-running without --reset is only safe
    for genuinely new data.
    """
    logger.warning("--reset: deleting all graph nodes and flushing Redis")
    from config import NEO4J_DATABASE

    async with neo4j_client.driver.session(database=NEO4J_DATABASE) as session:
        # Batched so a large graph does not blow up the transaction heap.
        while True:
            result = await session.run(
                "MATCH (n) WITH n LIMIT 50000 DETACH DELETE n RETURN count(n) AS n"
            )
            record = await result.single()
            deleted = record["n"] if record else 0
            logger.info("  deleted %d nodes", deleted)
            if not deleted:
                break

    if redis_client is not None:
        await redis_client.client.flushdb()
        logger.info("  redis flushed")


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    csv_path = Path(args.csv)
    patterns_path = Path(args.patterns)
    if not csv_path.exists():
        logger.error("csv not found: %s", csv_path)
        return 1
    if not patterns_path.exists():
        logger.error("patterns file not found: %s", patterns_path)
        return 1

    max_background = (
        None
        if str(args.max_background).lower() in ("none", "null", "-1")
        else int(args.max_background)
    )

    from db.neo4j import Neo4jClient

    neo4j_client = Neo4jClient()
    await neo4j_client.initialize()

    redis_client = None
    if not args.no_redis:
        from db.redis import RedisClient

        redis_client = RedisClient()
        await redis_client.initialize()

    try:
        if args.pagerank_only:
            # Recover path: the data is already in the graph, only the scores
            # are missing. Re-ingesting 5M rows to fix that would be absurd —
            # and with --reset it would also wipe what succeeded.
            started = time.monotonic()
            # window_days is deliberately absurd rather than anchored to the
            # dataset: export_flows_to_edges returns only src/dst/total_amount/
            # tx_count, never last_ts, so there is no way to derive the dataset's
            # own end from here. A 100,000-day window starts in the 1700s, which
            # includes every edge regardless of where "now" sits — so the anchor
            # cannot matter.
            logger.info("PageRank-only pass (window=100000d, includes all edges)")
            written = await neo4j_client.recompute_pagerank_full(
                window_days=100_000,
                export_timeout_seconds=args.export_timeout,
            )
            print(
                f"\nPageRank written for {written:,} accounts "
                f"in {time.monotonic() - started:.1f}s"
            )
            return 0

        if args.reset:
            await _reset_stores(neo4j_client, redis_client)

        # Must precede the load — see the module docstring.
        logger.info("Creating Neo4j constraints (index-backed MERGE) …")
        await neo4j_client.init_constraints()

        started = time.monotonic()
        stats = await ingest_for_training(
            csv_path=csv_path,
            patterns_path=patterns_path,
            neo4j_client=neo4j_client,
            redis_client=redis_client,
            typologies=args.typologies,
            max_background_rows=max_background,
            batch_size=args.batch_size,
            row_limit=args.row_limit,
            recompute_pagerank=not args.no_pagerank,
        )
        elapsed = time.monotonic() - started

        rate = stats.rows_scanned / elapsed if elapsed else 0.0
        print()
        print("=" * 72)
        print(f"INGEST COMPLETE in {elapsed:.1f}s ({rate:,.0f} rows/s scanned)")
        print("=" * 72)
        print(f"  rows scanned          : {stats.rows_scanned:,}")
        print(f"  bad rows skipped      : {stats.skipped_bad_rows:,}")
        print(f"  pattern rows written  : {stats.pattern_rows_written:,}")
        print(f"  background written    : {stats.background_rows_written:,}")
        print(f"  background skipped    : {stats.background_rows_skipped:,}")
        print(f"  TOTAL written         : {stats.total_written:,}")
        print(f"  redis rows            : {stats.redis_rows_written:,}")
        print(f"  pagerank scores       : {stats.pagerank_scores_written:,}")
        print(f"  pattern groups        : {stats.groups_loaded:,}")
        print(f"  pattern accounts      : {stats.pattern_accounts:,}")
        if stats.min_timestamp and stats.max_timestamp:
            print(
                f"  time span             : {stats.min_timestamp:%Y-%m-%d} .. "
                f"{stats.max_timestamp:%Y-%m-%d}"
            )
        print("  per-typology account slots:")
        for typology, count in sorted(
            stats.typology_counts.items(), key=lambda kv: -kv[1]
        ):
            print(f"    {typology:16s} {count:6,}")
        print("=" * 72)
        return 0
    finally:
        await neo4j_client.close()
        if redis_client is not None:
            await redis_client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
