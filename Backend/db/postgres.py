import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

# Postgres is the source of truth. Every normalized event is written here first
# with pending_graph_sync=TRUE; the graph (Neo4j) and cache (Redis) are derived
# state driven off this row. If the derived writes fail, the row stays pending
# and the @app.timer outbox worker re-drives it. Recovery reconstructs the event
# entirely from this row, so the schema carries everything the replay needs.

_pool: Optional[asyncpg.Pool] = None

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    event_id           TEXT        PRIMARY KEY,
    rail               TEXT        NOT NULL,
    sender_id          TEXT        NOT NULL,
    receiver_id        TEXT        NOT NULL,
    amount_cents       BIGINT      NOT NULL,
    amount_usd_cents   BIGINT      NOT NULL,
    currency           CHAR(3)     NOT NULL,
    timestamp_utc      TIMESTAMPTZ NOT NULL,
    status             TEXT        NOT NULL DEFAULT 'PENDING',
    raw_payload        JSONB       NOT NULL DEFAULT '{}',
    pending_graph_sync BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payments_sender   ON payments (sender_id);
CREATE INDEX IF NOT EXISTS idx_payments_receiver ON payments (receiver_id);
-- partial index: the outbox worker only ever scans unsynced rows
CREATE INDEX IF NOT EXISTS idx_payments_outbox   ON payments (created_at)
    WHERE pending_graph_sync = TRUE;
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "flowgraph"),
            user=os.getenv("POSTGRES_USER", "flowgraph"),
            password=os.getenv("POSTGRES_PASSWORD", "changeme"),
        )
    return _pool


async def init_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_SCHEMA)


def _parse_ts(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        ts = raw
    else:
        ts = datetime.fromisoformat(str(raw))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _row_to_event(row: asyncpg.Record) -> dict[str, Any]:
    # the replay unit: reconstruct the event dict for the graph/cache writers
    # from the durable Postgres row, so the agent and the outbox worker drive
    # byte-identical writes
    return {
        "event_id": row["event_id"],
        "rail": row["rail"],
        "sender_id": row["sender_id"],
        "receiver_id": row["receiver_id"],
        "amount_cents": row["amount_cents"],
        "amount_usd_cents": row["amount_usd_cents"],
        "currency": row["currency"],
        "timestamp_utc": row["timestamp_utc"],
        "status": row["status"],
        "raw_payload": json.loads(row["raw_payload"]) if isinstance(row["raw_payload"], str) else row["raw_payload"],
    }


async def write_payment(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Insert the canonical row. Returns the reconstructed event dict on a fresh
    insert, or None if the row already existed (a Kafka redelivery) — in which
    case the caller skips the graph/cache writes entirely (idempotency gate)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO payments (
                event_id, rail, sender_id, receiver_id,
                amount_cents, amount_usd_cents, currency, timestamp_utc,
                status, raw_payload, pending_graph_sync
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb, TRUE)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING *
            """,
            str(event["event_id"]),
            str(event["rail"]),
            event["sender_id"],
            event["receiver_id"],
            event["amount_cents"],
            event["amount_usd_cents"],
            event["currency"],
            _parse_ts(event["timestamp_utc"]),
            event.get("status", "PENDING"),
            json.dumps(event.get("raw_payload", {})),
        )
        return _row_to_event(row) if row is not None else None


async def clear_outbox(event_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE payments SET pending_graph_sync = FALSE WHERE event_id = $1",
            str(event_id),
        )


async def claim_stuck_rows(older_than_seconds: int = 60, limit: int = 100) -> list[dict[str, Any]]:
    """Claim rows the agent likely abandoned: still pending and older than the
    claim window. FOR UPDATE SKIP LOCKED lets multiple worker instances run
    without grabbing the same row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM payments
            WHERE pending_graph_sync = TRUE
              AND created_at < NOW() - ($1 || ' seconds')::interval
            ORDER BY created_at
            LIMIT $2
            FOR UPDATE SKIP LOCKED
            """,
            str(older_than_seconds),
            limit,
        )
        return [_row_to_event(r) for r in rows]
