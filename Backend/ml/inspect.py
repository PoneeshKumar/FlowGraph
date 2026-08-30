"""
Inspect the FeatureSet assembled from the live stores.

Answers the questions you actually need answered before training:

  - how many nodes and edges came back
  - which feature columns are dead (all zero) and therefore contributing nothing
  - how the weak labels are distributed
  - how weak labels compare against IBM AML ground truth, per typology

Run after an ingest:

    python3 -m ml.inspect
    python3 -m ml.inspect --no-ground-truth

A dead column is the signal to care about. If every Redis window is zero the
ingest skipped Redis; if pagerank_score is zero the PageRank pass did not run.
"""

import argparse
import asyncio
import logging
import sys

import numpy as np

logger = logging.getLogger("inspect")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-days", type=int, default=400)
    parser.add_argument(
        "--anchor-percentile",
        type=float,
        default=0.999,
        help="quantile of FLOWS_TO.last_ts to anchor the Redis windows at. "
             "Below 1.0 by default: the true max is often a near-empty tail "
             "(HI-Small has 1,108 of 5,078,345 transactions after 09-10), and "
             "anchoring there leaves the windows almost entirely zero.",
    )
    parser.add_argument(
        "--patterns",
        default="benchmarks/data/HI-Small_Patterns.txt",
        help="IBM AML ground-truth file",
    )
    parser.add_argument(
        "--no-ground-truth",
        action="store_true",
        help="skip the ground-truth comparison",
    )
    parser.add_argument(
        "--no-postgres",
        action="store_true",
        help="skip Postgres (no weak labels — every account unlabelled)",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )

    from db.neo4j import Neo4jClient
    from db.redis import RedisClient
    from ml.features import RISK_LEVEL_TO_CLASS, FeatureBuilder

    neo4j_client = Neo4jClient()
    await neo4j_client.initialize()
    redis_client = RedisClient()
    await redis_client.initialize()

    postgres_client = None
    if not args.no_postgres:
        from db.postgres import PostgresClient

        postgres_client = PostgresClient()
        try:
            await postgres_client.initialize()
        except Exception as exc:
            logger.warning("Postgres unavailable (%s) — continuing unlabelled", exc)
            postgres_client = None

    try:
        builder = FeatureBuilder(neo4j_client, redis_client, postgres_client)

        # Anchor to the dataset's own latest edge, not wall-clock now. This is
        # not cosmetic: the Redis windows are 1h/24h/7d wide, so on 2022 data a
        # now-anchored window matches nothing and silently zeroes 12 of the 29
        # feature columns.
        from datetime import datetime, timezone

        reference = None
        max_ts = await neo4j_client.get_flows_to_timestamp(
            percentile=args.anchor_percentile
        )
        if max_ts:
            reference = datetime.fromtimestamp(max_ts, tz=timezone.utc)
            logger.info(
                "Anchoring windows at p%.4g of last_ts: %s",
                args.anchor_percentile * 100,
                reference,
            )
        else:
            logger.warning("No FLOWS_TO timestamps found — falling back to now")

        # Wide enough to cover any dataset's own span once anchored.
        window_days = max(args.window_days, 2_000)

        feature_set = await builder.build(
            window_days=window_days, reference_time=reference
        )

        x = feature_set.x
        print()
        print("=" * 72)
        print("FEATURE SET")
        print("=" * 72)
        print(f"  nodes          : {feature_set.num_nodes:,}")
        print(f"  edges          : {feature_set.edge_index.shape[1]:,}")
        print(f"  features       : {feature_set.num_features}")
        if reference:
            print(f"  anchored at    : {reference:%Y-%m-%d %H:%M} UTC")
        print(f"  finite         : {bool(np.isfinite(x).all())}")

        print()
        print("  column                     nonzero        mean           max")
        print("  " + "-" * 62)
        dead = []
        for i, name in enumerate(feature_set.feature_names):
            column = x[:, i]
            nonzero = int(np.count_nonzero(column))
            if nonzero == 0:
                dead.append(name)
            share = nonzero / max(len(column), 1)
            print(
                f"  {name:24s} {nonzero:>9,} ({share:5.1%}) "
                f"{float(column.mean()):>12.4g} {float(column.max()):>12.4g}"
            )

        print()
        if dead:
            print(f"  DEAD COLUMNS ({len(dead)}) — contributing nothing:")
            for name in dead:
                print(f"    - {name}")
        else:
            print("  no dead columns")

        print()
        print("=" * 72)
        print("WEAK LABELS (from risk_flags, CYCLE only)")
        print("=" * 72)
        class_names = {v: k for k, v in RISK_LEVEL_TO_CLASS.items()}
        for class_index in sorted(class_names):
            count = int((feature_set.y == class_index).sum())
            print(f"  {class_names[class_index]:9s} : {count:,}")
        labelled = int(feature_set.labelled_mask.sum())
        print(f"  labelled  : {labelled:,} of {feature_set.num_nodes:,}")
        if labelled == 0:
            print(
                "  NOTE: no weak labels. The detectors have not run over this "
                "graph yet — run cycle detection to populate risk_flags."
            )

        if not args.no_ground_truth:
            from pathlib import Path

            if not Path(args.patterns).exists():
                print(f"\n  ground truth not found at {args.patterns}")
            else:
                from ml.evaluate import ALL_TYPOLOGIES, load_ground_truth

                ground_truth = load_ground_truth(args.patterns)
                truth = ground_truth.labels_for(feature_set.node_ids)

                print()
                print("=" * 72)
                print("GROUND TRUTH COVERAGE (IBM AML patterns)")
                print("=" * 72)
                print(f"  laundering accounts in graph : {int(truth.sum()):,}")
                print(
                    f"  prevalence                   : "
                    f"{truth.sum() / max(len(truth), 1):.3%}"
                )
                print()
                print("  typology            in graph   of total")
                print("  " + "-" * 42)
                for typology in ALL_TYPOLOGIES:
                    accounts = ground_truth.accounts_by_typology.get(typology, set())
                    present = int(
                        ground_truth.typology_labels_for(
                            feature_set.node_ids, typology
                        ).sum()
                    )
                    share = present / max(len(accounts), 1)
                    print(
                        f"  {typology:18s} {present:>8,}   {share:6.1%} "
                        f"of {len(accounts):,}"
                    )
        print("=" * 72)
        return 0
    finally:
        await neo4j_client.close()
        await redis_client.close()
        if postgres_client is not None:
            await postgres_client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
