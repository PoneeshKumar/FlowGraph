"""
Stream an IBM AML transaction CSV into Neo4j + Redis for GNN training.

WHY THIS EXISTS ALONGSIDE benchmarks/ibm_aml/ingestor.py
--------------------------------------------------------
That ingestor is tuned for benchmarking cycle detection, and the published
72%/87% recall figures depend on its exact sampling. It is left untouched.

It is also the wrong shape for training data, in two ways:

  1. It loads CYCLE accounts only. The runner calls load_cycle_groups, which
     returns 54 of the 370 labelled laundering attempts. The other 316 —
     FAN-OUT, FAN-IN, BIPARTITE, STACK, GATHER-SCATTER, SCATTER-GATHER — never
     reach the graph. Those are the typologies with no closed loop, so they are
     precisely the ones a GNN could learn that cycle detection cannot express
     at any depth. Loading them multiplies labelled positive accounts roughly
     tenfold.
  2. Background traffic is capped at MAX_BACKGROUND_ROWS = 200_000. Of 5.08M
     transactions in HI-Small, only about 4% is ever written.

This loader takes all typologies, makes the background cap a parameter, writes
through bulk_upsert_transactions, and populates Redis so the time-window
features are not all zero.

DELIBERATE OUTBOX BYPASS
------------------------
Writes go straight to Neo4j rather than through the Postgres outbox. The outbox
exists so the graph is never ahead of the payment ledger for *live* payments;
this is offline loading of a historical research dataset, where that invariant
is meaningless and the round trips would dominate runtime. The existing
benchmark ingestor makes the same call.

BACKGROUND ROWS ARE TAKEN IN FILE ORDER
---------------------------------------
Not randomly sampled. Two reasons: a reservoir sample would need the whole
candidate set resident (several GB at 5M rows), and the CSV is chronological, so
file order gives a contiguous time slice — exactly what a time-based train/test
split wants. Random sampling would scatter holes through the timeline.
"""

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger(__name__)

# Every typology in HI-Small_Patterns.txt.
ALL_TYPOLOGIES: Tuple[str, ...] = (
    "CYCLE",
    "FAN-OUT",
    "FAN-IN",
    "GATHER-SCATTER",
    "SCATTER-GATHER",
    "BIPARTITE",
    "STACK",
    "RANDOM",
)

LOG_EVERY = 250_000


@dataclass
class TrainingIngestStats:
    """What actually got loaded."""

    rows_scanned: int = 0
    skipped_bad_rows: int = 0
    pattern_rows_written: int = 0
    background_rows_written: int = 0
    background_rows_skipped: int = 0
    redis_rows_written: int = 0
    pagerank_scores_written: int = 0
    pattern_accounts: int = 0
    groups_loaded: int = 0
    typology_counts: Dict[str, int] = field(default_factory=dict)
    min_timestamp: Optional[datetime] = None
    max_timestamp: Optional[datetime] = None

    @property
    def total_written(self) -> int:
        return self.pattern_rows_written + self.background_rows_written

    def summary(self) -> str:
        span = ""
        if self.min_timestamp and self.max_timestamp:
            span = (
                f" | span {self.min_timestamp:%Y-%m-%d}"
                f" .. {self.max_timestamp:%Y-%m-%d}"
            )
        return (
            f"scanned={self.rows_scanned} written={self.total_written} "
            f"(pattern={self.pattern_rows_written} "
            f"background={self.background_rows_written}) "
            f"groups={self.groups_loaded} pattern_accounts={self.pattern_accounts} "
            f"redis={self.redis_rows_written} pagerank={self.pagerank_scores_written}"
            f"{span}"
        )


def load_pattern_accounts(
    patterns_path: Union[str, Path],
    typologies: Iterable[str] = ALL_TYPOLOGIES,
) -> Tuple[Set[str], int, Dict[str, int]]:
    """Account keys belonging to any labelled laundering pattern.

    Returns (account_keys, group_count, per-typology account counts). Keys are
    the same _account_key sha256 hashes the CSV rows produce, so they match
    Neo4j node ids with no mapping step.
    """
    from benchmarks.ibm_aml.patterns import load_pattern_groups

    wanted = [t.strip().upper() for t in typologies]
    groups = load_pattern_groups(patterns_path, wanted)

    accounts: Set[str] = set()
    per_typology: Dict[str, int] = {}
    for group in groups:
        accounts |= group.account_set
        key = group.typology.upper()
        per_typology[key] = per_typology.get(key, 0) + len(group.accounts)

    logger.info(
        "Loaded %d pattern groups across %d typologies -> %d distinct accounts",
        len(groups),
        len(per_typology),
        len(accounts),
    )
    return accounts, len(groups), per_typology


async def ingest_for_training(
    csv_path: Union[str, Path],
    patterns_path: Union[str, Path],
    neo4j_client: Any,
    redis_client: Optional[Any] = None,
    typologies: Iterable[str] = ALL_TYPOLOGIES,
    max_background_rows: Optional[int] = 2_000_000,
    batch_size: int = 1000,
    row_limit: Optional[int] = None,
    recompute_pagerank: bool = True,
) -> TrainingIngestStats:
    """Stream a transaction CSV into Neo4j (and optionally Redis).

    Single pass, flushing every batch_size rows, so memory stays flat no matter
    how large the file is.

    Every row touching a labelled pattern account is written. Non-pattern rows
    are written in file order until max_background_rows is reached.

    Args:
        csv_path:            HI-Small_Trans.csv or any same-schema variant
                             (HI-Medium, LI-Small, ...).
        patterns_path:       Matching HI-*_Patterns.txt.
        neo4j_client:        Initialized Neo4jClient.
        redis_client:        Initialized RedisClient, or None to skip the
                             time-window features. Strongly recommended:
                             without it, 12 of the 29 features are zero.
        typologies:          Which laundering typologies to treat as patterns.
        max_background_rows: Cap on non-pattern rows. None means no cap — every
                             row in the file gets written.
        batch_size:          Rows per Neo4j/Redis round trip.
        row_limit:           Stop after scanning this many CSV rows. For smoke
                             runs; None reads the whole file.
        recompute_pagerank:  Run one full-graph sparse PageRank at the end.
                             bulk_upsert_transactions skips the per-transaction
                             PageRank the streaming path does, so without this
                             pagerank_score stays 0 for every account.

    Returns:
        TrainingIngestStats
    """
    from benchmarks.ibm_aml.ingestor import _row_from_parts, _row_to_transfer

    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} not found. Download the IBM AML dataset from Kaggle "
            f"(ealtman2019/ibm-transactions-for-anti-money-laundering-aml) and "
            f"place it in benchmarks/data/."
        )
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    stats = TrainingIngestStats()
    pattern_accounts, group_count, per_typology = load_pattern_accounts(
        patterns_path, typologies
    )
    stats.pattern_accounts = len(pattern_accounts)
    stats.groups_loaded = group_count
    stats.typology_counts = per_typology

    if not pattern_accounts:
        logger.warning(
            "No pattern accounts loaded — every row will be treated as "
            "background and the graph will have no positive labels"
        )

    background_budget = (
        float("inf") if max_background_rows is None else int(max_background_rows)
    )

    buffer: List[Dict[str, Any]] = []

    async def flush() -> None:
        if not buffer:
            return
        await neo4j_client.bulk_upsert_transactions(buffer, batch_size=batch_size)
        if redis_client is not None:
            stats.redis_rows_written += await redis_client.bulk_add_edges_to_timeseries(
                buffer, batch_size=batch_size
            )
        buffer.clear()

    logger.info(
        "Training ingest starting | csv=%s | pattern_accounts=%d | "
        "background_cap=%s",
        csv_path.name,
        len(pattern_accounts),
        max_background_rows if max_background_rows is not None else "none",
    )

    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)  # header has duplicate "Account" columns — read positionally

        for index, parts in enumerate(reader):
            if row_limit is not None and index >= row_limit:
                break
            stats.rows_scanned += 1

            transfer = _row_to_transfer(_row_from_parts(parts))
            if transfer is None:
                stats.skipped_bad_rows += 1
                continue

            is_pattern = (
                transfer["sender_id"] in pattern_accounts
                or transfer["receiver_id"] in pattern_accounts
            )

            if is_pattern:
                stats.pattern_rows_written += 1
            elif background_budget > 0:
                background_budget -= 1
                stats.background_rows_written += 1
            else:
                stats.background_rows_skipped += 1
                continue

            timestamp = transfer["timestamp_utc"]
            if isinstance(timestamp, datetime):
                if stats.min_timestamp is None or timestamp < stats.min_timestamp:
                    stats.min_timestamp = timestamp
                if stats.max_timestamp is None or timestamp > stats.max_timestamp:
                    stats.max_timestamp = timestamp

            buffer.append(transfer)
            if len(buffer) >= batch_size:
                await flush()

            if stats.rows_scanned % LOG_EVERY == 0:
                logger.info(
                    "  scanned %d | pattern=%d background=%d skipped=%d",
                    stats.rows_scanned,
                    stats.pattern_rows_written,
                    stats.background_rows_written,
                    stats.background_rows_skipped,
                )

    await flush()

    if recompute_pagerank:
        # Anchor to the dataset's own end, not wall-clock now: this data is from
        # 2022, so a now-anchored window would classify every edge as stale and
        # score nothing.
        reference = stats.max_timestamp or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        span_days = 1
        if stats.min_timestamp is not None:
            span_days = max((reference - stats.min_timestamp).days + 1, 1)

        logger.info(
            "Recomputing full-graph PageRank (window=%dd anchored at %s) …",
            span_days,
            reference.date(),
        )
        stats.pagerank_scores_written = await neo4j_client.recompute_pagerank_full(
            window_days=span_days, reference_time=reference
        )

    logger.info("Training ingest complete | %s", stats.summary())
    return stats
