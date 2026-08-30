"""
Run the Louvain community batch over the graph currently in Neo4j.

Populates Account.community_id, which the GNN feature builder turns into the
three community_* columns. Without this they are dead columns.

    python3 -m ml.datasets.run_louvain
    LOUVAIN_ENGINE=leiden python3 -m ml.datasets.run_louvain

Distinct from `python -m fraud.community_detector`, which injects a synthetic
gather-scatter community first as a demo. This runs the batch over whatever is
already loaded.

The window is anchored on the graph's own latest FLOWS_TO timestamp rather than
wall-clock now. On a historical dataset (IBM AML is 2022) a now-anchored window
excludes every edge and the batch finds nothing.
"""

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("run_louvain")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export-timeout",
        type=float,
        default=1800.0,
        help="seconds for the FLOWS_TO export; the 120s config default is not "
             "enough for a full-size graph",
    )
    parser.add_argument(
        "--anchor-percentile",
        type=float,
        default=1.0,
        help="quantile of FLOWS_TO.last_ts to anchor the window at. 1.0 (the "
             "max) is right here, unlike the Redis windows: LOUVAIN_WINDOW_DAYS "
             "is 30 days wide, so a sparse tail cannot starve it",
    )
    parser.add_argument(
        "--no-postgres",
        action="store_true",
        help="skip Postgres: community_id node properties are still written, "
             "but no risk_flags rows and no cross-detector overlap scoring",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    from config import LOUVAIN_ENGINE, LOUVAIN_WINDOW_DAYS
    from db.neo4j import Neo4jClient
    from fraud.community_detector import CommunityDetector

    neo4j_client = Neo4jClient()
    await neo4j_client.initialize()

    postgres_client = None
    if not args.no_postgres:
        from db.postgres import PostgresClient

        postgres_client = PostgresClient()
        try:
            await postgres_client.initialize()
        except Exception as exc:
            logger.warning(
                "Postgres unavailable (%s) — continuing without flag persistence",
                exc,
            )
            postgres_client = None

    try:
        anchor = await neo4j_client.get_flows_to_timestamp(
            percentile=args.anchor_percentile
        )
        if anchor:
            reference = datetime.fromtimestamp(anchor, tz=timezone.utc)
        else:
            logger.warning("No FLOWS_TO timestamps found — anchoring at now")
            reference = datetime.now(timezone.utc)

        window_start = reference - timedelta(days=LOUVAIN_WINDOW_DAYS)
        logger.info(
            "Louvain batch | engine=%s | window %s .. %s (%d days)",
            LOUVAIN_ENGINE,
            window_start.date(),
            reference.date(),
            LOUVAIN_WINDOW_DAYS,
        )

        detector = CommunityDetector(neo4j_client, postgres_client)
        started = time.monotonic()
        result = await detector.run(
            reference_time=reference,
            export_timeout_seconds=args.export_timeout,
        )
        elapsed = time.monotonic() - started

        flags = result.get("flags", [])
        by_level: dict = {}
        for flag in flags:
            level = flag.get("risk_level", "unknown")
            by_level[level] = by_level.get(level, 0) + 1

        print()
        print("=" * 72)
        print(f"LOUVAIN COMPLETE in {elapsed:.1f}s")
        print("=" * 72)
        print(f"  communities kept      : {result.get('communities', 0):,}")
        print(f"  community_id written  : {result.get('assignments', 0):,} accounts")
        print(f"  risk_flags raised     : {len(flags):,}")
        for level in ("critical", "high", "medium", "low"):
            if level in by_level:
                print(f"    {level:9s}: {by_level[level]:,}")
        print("=" * 72)
        return 0
    finally:
        await neo4j_client.close()
        if postgres_client is not None:
            await postgres_client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
