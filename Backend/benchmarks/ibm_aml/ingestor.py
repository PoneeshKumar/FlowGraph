"""
IBM AML CSV ingestor.

Loads HI-Small_Trans.csv and writes TRANSFER edges to Neo4j via the real
production writer (upsert_transaction_graph). No special-case code — the
harness exercises the same code path as live traffic.

Two passes:
  1. Cycle transactions — all rows belonging to the labeled CYCLE groups
  2. Background sample  — a capped random sample of Is Laundering == 0 rows

Usage:
    from benchmarks.ibm_aml.ingestor import ingest, IngestStats
    stats = await ingest(csv_path, cycle_groups, neo4j_client=neo4j)
"""

from __future__ import annotations

import csv
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from benchmarks.ibm_aml.patterns import CycleGroup, _account_key, _parse_ts, _row_from_parts

logger = logging.getLogger(__name__)

# Cap on background rows regardless of background_ratio to keep Neo4j ingest manageable
MAX_BACKGROUND_ROWS = 200_000

# Log progress every N rows
LOG_EVERY = 5_000


def _sample_without_replacement(
    rng: "np.random.Generator", population: list[dict], k: int
) -> list[dict]:
    """Sample k items from population without replacement, via index choice
    (numpy's Generator.choice only samples 1-D arrays of scalars directly,
    not a list of dicts)."""
    idx = rng.choice(len(population), size=k, replace=False)
    return [population[i] for i in idx]


@dataclass
class IngestStats:
    cycle_txns_written: int = 0
    background_written: int = 0
    skipped_duplicates: int = 0
    skipped_bad_rows: int = 0


def _make_txn_id(ts_raw: str, from_key: str, to_key: str, amount_paid: str) -> str:
    """
    Deterministic transaction ID from key fields.

    Required for outbox idempotency: retried ingests of the same row must produce
    the same txn_id so that the Neo4j MERGE on txn_id is idempotent.
    """
    return hashlib.sha256(
        f"{ts_raw}|{from_key}|{to_key}|{amount_paid}".encode()
    ).hexdigest()[:40]


def _row_to_transfer(row: dict) -> Optional[dict]:
    """
    Convert one IBM AML CSV row to our TRANSFER model.

    Returns None if required fields are missing or unparseable.
    """
    try:
        from_key = _account_key(row["From Bank"], row["Account"])
        to_key   = _account_key(row["To Bank"],   row["Account.1"])
        amount   = int(float(row["Amount Paid"]) * 100)
        ts       = _parse_ts(row["Timestamp"])
        currency = row.get("Payment Currency", "UNKNOWN").strip()
        ts_raw   = row["Timestamp"].strip()
    except (KeyError, ValueError, TypeError):
        return None

    if ts is None or amount <= 0:
        return None

    return {
        "sender_id":      from_key,
        "receiver_id":    to_key,
        "amount_cents":   amount,
        "timestamp_utc":  ts,
        "rail":           f"IBM_AML_{currency}",   # encodes currency in rail field
        "event_type":     "SETTLEMENT",
        "transaction_id": _make_txn_id(ts_raw, from_key, to_key, row["Amount Paid"]),
        "idempotency_key": _make_txn_id(ts_raw, from_key, to_key, row["Amount Paid"]),
        "currency":       currency,
        "is_laundering":  row.get("Is Laundering", "0").strip() == "1",
    }


async def _write_batch(
    batch: list[dict],
    neo4j_client: Any,
    postgres_client: Optional[Any],
    stats: IngestStats,
) -> None:
    """Write a batch of transfer rows to Neo4j (and optionally Postgres)."""
    for transfer in batch:
        try:
            await neo4j_client.upsert_transaction_graph(
                sender_id=transfer["sender_id"],
                receiver_id=transfer["receiver_id"],
                amount_cents=transfer["amount_cents"],
                timestamp_utc=transfer["timestamp_utc"],
                rail=transfer["rail"],
                event_type=transfer["event_type"],
                transaction_id=transfer["transaction_id"],
                idempotency_key=transfer["idempotency_key"],
            )
        except Exception as e:
            logger.warning("Neo4j write failed for txn %s: %s", transfer["transaction_id"], e)
            stats.skipped_duplicates += 1
            continue

        if postgres_client:
            try:
                await postgres_client.save_transaction(
                    transaction_id=transfer["transaction_id"],
                    rail=transfer["rail"],
                    event_type=transfer["event_type"],
                    status="SETTLED",
                    sender_id=transfer["sender_id"],
                    receiver_id=transfer["receiver_id"],
                    amount_cents=transfer["amount_cents"],
                    currency=transfer["currency"],
                    timestamp_utc=transfer["timestamp_utc"],
                    raw_payload={"source": "ibm_aml_benchmark"},
                )
            except Exception as e:
                logger.debug("Postgres write skipped for %s: %s", transfer["transaction_id"], e)


async def ingest(
    csv_path: str | Path,
    cycle_groups: list[CycleGroup],
    neo4j_client: Any,
    postgres_client: Optional[Any] = None,
    background_ratio: float = 5.0,
    batch_size: int = 500,
    seed: int = 42,
) -> IngestStats:
    """
    Load IBM AML CSV and write TRANSFER edges to Neo4j (and optionally Postgres).

    Pass 1 — cycle transactions:
        All rows whose (From Bank, Account) or (To Bank, Account.1) appear in any
        labeled CycleGroup. Ensures the full cycle topology is present in Neo4j.

    Pass 2 — background sample:
        A random sample of Is Laundering == 0 rows, capped at
        min(background_ratio × cycle_txn_count, MAX_BACKGROUND_ROWS). Provides
        normal traffic so false positives can be measured.

    Args:
        csv_path:          Path to HI-Small_Trans.csv
        cycle_groups:      Labeled CYCLE groups (from patterns.load_cycle_groups)
        neo4j_client:      Initialized Neo4jClient
        postgres_client:   Initialized PostgresClient, or None for neo4j-only mode
        background_ratio:  Multiplier on cycle count for background sample size
        batch_size:        How many rows to write before logging progress
        seed:              Random seed for background sample reproducibility

    Returns:
        IngestStats
    """
    csv_path = Path(csv_path)
    rng = np.random.default_rng(seed)
    stats = IngestStats()

    # Build the set of account keys that appear in any cycle group
    cycle_account_keys: set[str] = set()
    for g in cycle_groups:
        cycle_account_keys.update(g.accounts)

    logger.info(
        "Ingestor starting | cycle groups=%d | cycle accounts=%d | csv=%s",
        len(cycle_groups),
        len(cycle_account_keys),
        csv_path.name,
    )

    # --- Pass 1: collect and write cycle rows ---
    cycle_rows: list[dict] = []
    background_candidates: list[dict] = []

    logger.info("Pass 1: scanning CSV for cycle transactions …")
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header (has duplicate "Account" cols — read positionally)
        for i, parts in enumerate(reader):
            row = _row_from_parts(parts)
            transfer = _row_to_transfer(row)
            if transfer is None:
                stats.skipped_bad_rows += 1
                continue

            is_cycle_row = (
                transfer["sender_id"]   in cycle_account_keys
                or transfer["receiver_id"] in cycle_account_keys
            )

            if is_cycle_row:
                cycle_rows.append(transfer)
            elif not transfer["is_laundering"]:
                # Reservoir-sample background candidates (avoid storing all 5M rows)
                background_candidates.append(transfer)
                if len(background_candidates) > MAX_BACKGROUND_ROWS * 3:
                    # Trim to avoid unbounded memory: keep a rolling sample
                    background_candidates = _sample_without_replacement(
                        rng, background_candidates, MAX_BACKGROUND_ROWS
                    )

            if (i + 1) % LOG_EVERY == 0:
                logger.info(
                    "  scanned %d rows | cycle=%d background_candidates=%d",
                    i + 1, len(cycle_rows), len(background_candidates),
                )

    logger.info("Writing %d cycle transactions to Neo4j …", len(cycle_rows))
    batch: list[dict] = []
    for i, transfer in enumerate(cycle_rows):
        batch.append(transfer)
        if len(batch) >= batch_size:
            await _write_batch(batch, neo4j_client, postgres_client, stats)
            stats.cycle_txns_written += len(batch)
            batch = []
            if (stats.cycle_txns_written) % LOG_EVERY == 0:
                logger.info("  wrote %d cycle rows", stats.cycle_txns_written)
    if batch:
        await _write_batch(batch, neo4j_client, postgres_client, stats)
        stats.cycle_txns_written += len(batch)

    # --- Pass 2: background sample ---
    bg_target = min(
        int(len(cycle_rows) * background_ratio),
        MAX_BACKGROUND_ROWS,
    )
    if bg_target > 0 and background_candidates:
        bg_sample = _sample_without_replacement(
            rng, background_candidates, min(bg_target, len(background_candidates))
        )
        logger.info("Writing %d background transactions to Neo4j …", len(bg_sample))
        batch = []
        for transfer in bg_sample:
            batch.append(transfer)
            if len(batch) >= batch_size:
                await _write_batch(batch, neo4j_client, postgres_client, stats)
                stats.background_written += len(batch)
                batch = []
        if batch:
            await _write_batch(batch, neo4j_client, postgres_client, stats)
            stats.background_written += len(batch)

    logger.info(
        "Ingest complete | cycle=%d | background=%d | bad_rows=%d | skipped=%d",
        stats.cycle_txns_written,
        stats.background_written,
        stats.skipped_bad_rows,
        stats.skipped_duplicates,
    )
    return stats
