"""
PostgreSQL client for FlowGraph.

Handles canonical transaction storage and outbox table management.
Provides atomic dual-write semantics: transaction + outbox inserted in single transaction.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import asyncpg
from asyncpg import Pool, Connection
from config import (
    POSTGRES_DSN,
    POSTGRES_POOL_SIZE,
    POSTGRES_POOL_TIMEOUT,
    OUTBOX_TABLE_NAME,
    TRANSACTIONS_TABLE_NAME,
)

logger = logging.getLogger(__name__)


class PostgresClient:
    """Async PostgreSQL client for transactions and outbox management."""

    def __init__(self):
        self.pool: Optional[Pool] = None

    async def initialize(self) -> None:
        """Create connection pool."""
        try:
            # Parse DSN to extract host/port for logging
            # Format: postgresql+asyncpg://user:pass@host:port/dbname
            self.pool = await asyncpg.create_pool(
                self._parse_dsn(POSTGRES_DSN),
                min_size=5,
                max_size=POSTGRES_POOL_SIZE,
                command_timeout=POSTGRES_POOL_TIMEOUT,
            )
            logger.info("PostgreSQL connection pool initialized")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL pool: {e}")
            raise

    async def close(self) -> None:
        """Close connection pool."""
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL connection pool closed")

    @staticmethod
    def _parse_dsn(dsn: str) -> str:
        """Extract asyncpg-compatible DSN from sqlalchemy-style DSN."""
        # Input: postgresql+asyncpg://user:pass@host:port/dbname
        # Output: postgresql://user:pass@host:port/dbname
        return dsn.replace("postgresql+asyncpg://", "postgresql://")

    @asynccontextmanager
    async def _get_connection(self):
        """Context manager to get a connection from the pool."""
        if not self.pool:
            raise RuntimeError("PostgresClient not initialized. Call initialize() first.")
        conn = await self.pool.acquire()
        try:
            yield conn
        finally:
            await self.pool.release(conn)

    @asynccontextmanager
    async def _conn_or(self, conn: Optional[Connection]):
        """Use the caller's connection — so the write joins their transaction — or
        take one from the pool for a standalone write."""
        if conn is not None:
            yield conn
        else:
            async with self._get_connection() as pooled:
                yield pooled

    @asynccontextmanager
    async def transaction(self):
        """One connection inside one transaction, for writes that must land together.

        The outbox pattern depends on this: a payment row and its outbox row have to
        commit atomically, or a crash between them strands a payment in Postgres that
        never reaches the graph. Pass the yielded connection to each write:

            async with pg.transaction() as conn:
                await pg.save_transaction(..., conn=conn)
                await pg.insert_outbox(..., conn=conn)
        """
        async with self._get_connection() as conn:
            async with conn.transaction():
                yield conn

    # ==================== TRANSACTION MANAGEMENT ====================

    async def save_transaction(
        self,
        transaction_id: str,
        rail: str,
        event_type: str,
        status: str,
        sender_id: str,
        receiver_id: str,
        amount_cents: int,
        currency: str,
        timestamp_utc: datetime,
        raw_payload: Dict[str, Any],
        schema_version: int = 1,
        authorization_code: Optional[str] = None,
        conn: Optional[Connection] = None,
    ) -> None:
        """
        Save a payment transaction to the transactions table.

        Pass `conn` from `transaction()` to commit this together with the outbox
        row; omit it for a standalone write (the bulk ingest does this — it writes
        the graph directly and needs no outbox entry).

        Args:
            transaction_id: UUID of the transaction
            rail: Payment rail (CARD, WIRE, ACH, CRYPTO)
            event_type: Event type (AUTH, SETTLEMENT, etc.)
            status: Transaction status (PENDING, SETTLED, DECLINED, ORPHANED)
            sender_id: Hashed sender identifier
            receiver_id: Hashed receiver identifier
            amount_cents: Amount in cents (integer)
            currency: 3-char ISO currency code
            timestamp_utc: UTC timestamp of the transaction
            raw_payload: Original raw event payload
            schema_version: Schema version (default 1)
            authorization_code: Auth code for settlement matching (optional)
            conn: Join this existing transaction instead of taking a pooled connection
        """
        query = f"""
        INSERT INTO {TRANSACTIONS_TABLE_NAME} (
            id, rail, event_type, status, sender_id, receiver_id,
            amount_cents, currency, timestamp_utc, raw_payload,
            schema_version, authorization_code, created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
        )
        ON CONFLICT (id) DO UPDATE SET
            status = $4,
            updated_at = CURRENT_TIMESTAMP
        """

        async with self._conn_or(conn) as c:
            await c.execute(
                query,
                transaction_id,
                rail,
                event_type,
                status,
                sender_id,
                receiver_id,
                amount_cents,
                currency,
                timestamp_utc,
                json.dumps(raw_payload),
                schema_version,
                authorization_code,
                datetime.utcnow(),
            )

    async def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a transaction by ID."""
        query = f"SELECT * FROM {TRANSACTIONS_TABLE_NAME} WHERE id = $1"
        async with self._get_connection() as conn:
            row = await conn.fetchrow(query, transaction_id)
            if row:
                return dict(row)
            return None

    # ==================== OUTBOX MANAGEMENT ====================

    async def insert_outbox(
        self,
        transaction_id: str,
        idempotency_key: str,
        event_payload: Dict[str, Any],
        neo4j_write_payload: Optional[Dict[str, Any]] = None,
        conn: Optional[Connection] = None,
    ) -> None:
        """
        Insert an outbox entry for a transaction.

        Must be called with the `conn` from `transaction()`, alongside
        save_transaction() — that is the whole point of the outbox. Writing the two
        rows in separate transactions reintroduces the gap the pattern exists to
        close: a crash in between leaves a payment that never reaches the graph.

        Args:
            transaction_id: UUID of the transaction
            idempotency_key: Unique key to prevent duplicate retries
            event_payload: Serialized transaction data for replay
            neo4j_write_payload: Pre-computed Neo4j MERGE payload (optional)
            conn: Join this existing transaction instead of taking a pooled connection
        """
        query = f"""
        INSERT INTO {OUTBOX_TABLE_NAME} (
            transaction_id, idempotency_key, event_payload, 
            neo4j_write_payload, status, retry_count, created_at
        ) VALUES ($1, $2, $3, $4, 'pending', 0, CURRENT_TIMESTAMP)
        ON CONFLICT (idempotency_key) DO NOTHING
        """

        async with self._conn_or(conn) as c:
            await c.execute(
                query,
                transaction_id,
                idempotency_key,
                json.dumps(event_payload),
                json.dumps(neo4j_write_payload) if neo4j_write_payload else None,
            )

    async def fetch_pending_outbox(
        self, batch_size: int = 50, max_age_seconds: int = 3600
    ) -> List[Dict[str, Any]]:
        """
        Fetch pending outbox records for sync.
        
        Prioritizes older records and those with fewer retries.
        
        Args:
            batch_size: Maximum records to fetch per poll
            max_age_seconds: Only fetch records created within last N seconds (optional)
        
        Returns:
            List of outbox records with status='pending'
        """
        query = f"""
        SELECT id, transaction_id, idempotency_key, event_payload,
               neo4j_write_payload, retry_count, last_error, created_at
        FROM {OUTBOX_TABLE_NAME}
        WHERE status = 'pending'
          AND (last_retry_at IS NULL OR last_retry_at < CURRENT_TIMESTAMP - INTERVAL '10 seconds' * retry_count)
        ORDER BY retry_count ASC, created_at ASC
        LIMIT $1
        """

        async with self._get_connection() as conn:
            rows = await conn.fetch(query, batch_size)
            return [dict(row) for row in rows]

    async def mark_outbox_synced(self, outbox_id: int) -> None:
        """Mark an outbox record as successfully synced."""
        query = f"""
        UPDATE {OUTBOX_TABLE_NAME}
        SET status = 'synced', synced_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = $1
        """
        async with self._get_connection() as conn:
            await conn.execute(query, outbox_id)

    async def mark_outbox_failed(self, outbox_id: int, error_message: str) -> None:
        """Mark an outbox record as failed (max retries exceeded)."""
        query = f"""
        UPDATE {OUTBOX_TABLE_NAME}
        SET status = 'failed', last_error = $2, updated_at = CURRENT_TIMESTAMP
        WHERE id = $1
        """
        async with self._get_connection() as conn:
            await conn.execute(query, outbox_id, error_message)

    async def increment_outbox_retry(
        self, outbox_id: int, error_message: Optional[str] = None
    ) -> None:
        """Increment retry count and update last_retry_at timestamp."""
        query = f"""
        UPDATE {OUTBOX_TABLE_NAME}
        SET retry_count = retry_count + 1,
            last_retry_at = CURRENT_TIMESTAMP,
            last_error = COALESCE($2, last_error),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = $1
        """
        async with self._get_connection() as conn:
            await conn.execute(query, outbox_id, error_message)

    async def get_outbox_stats(self) -> Dict[str, Any]:
        """Get current outbox statistics."""
        query = f"""
        SELECT
            status,
            COUNT(*) as count,
            EXTRACT(EPOCH FROM MAX(CURRENT_TIMESTAMP - created_at)) as max_age_seconds,
            EXTRACT(EPOCH FROM AVG(CURRENT_TIMESTAMP - created_at)) as avg_age_seconds
        FROM {OUTBOX_TABLE_NAME}
        WHERE status IN ('pending', 'synced', 'failed')
        GROUP BY status
        """

        async with self._get_connection() as conn:
            rows = await conn.fetch(query)
            return {row["status"]: dict(row) for row in rows}

    # ==================== RISK FLAGS ====================

    async def upsert_risk_flag(
        self,
        flag_type: str,
        fingerprint: str,
        account_ids: List[str],
        risk_level: str,
        risk_score: float,
        explanation: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Persist a fraud detection result, idempotent on fingerprint.

        A fingerprint uniquely identifies a logical flag (e.g. the canonical
        form of a cycle ring). On conflict the existing row is updated:
        detection_count increments, last_detected_at refreshes, and the
        current risk assessment (level / score / explanation) overwrites the
        old one — so a re-detected flag always reflects the latest evaluation.

        explanation must never be None: it is a regulatory requirement that
        every flag carries a human-readable reason (CLAUDE.md convention).

        Args:
            flag_type:    Detector type ('CYCLE', 'STRUCTURING', 'CTR', …)
            fingerprint:  Stable unique key for this logical flag (sha256 hex)
            account_ids:  List of account IDs involved
            risk_level:   'low' | 'medium' | 'high' | 'critical'
            risk_score:   Numeric score (0.0–1.0)
            explanation:  Natural-language reason (required)
            details:      Raw detector output (amounts, timestamps, hop count, …)
        """
        if not explanation:
            raise ValueError("explanation must not be empty — regulatory requirement")

        query = """
        INSERT INTO risk_flags (
            flag_type, fingerprint, account_ids, risk_level, risk_score,
            explanation, details, status, first_detected_at, last_detected_at,
            detection_count, created_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7::jsonb, 'open', NOW(), NOW(), 1, NOW()
        )
        ON CONFLICT (fingerprint) DO UPDATE SET
            last_detected_at = NOW(),
            detection_count  = risk_flags.detection_count + 1,
            risk_level       = EXCLUDED.risk_level,
            risk_score       = EXCLUDED.risk_score,
            explanation      = EXCLUDED.explanation,
            details          = EXCLUDED.details
        """

        async with self._get_connection() as conn:
            await conn.execute(
                query,
                flag_type,
                fingerprint,
                account_ids,
                risk_level,
                risk_score,
                explanation,
                json.dumps(details) if details else None,
            )

    async def get_risk_flags(
        self,
        flag_type: Optional[str] = None,
        min_level: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Query risk flags, optionally filtered by type, minimum level, or status.

        Risk levels ordered: low < medium < high < critical.

        Args:
            flag_type: Filter to a specific detector ('CYCLE', 'STRUCTURING', …)
            min_level: Return only flags at or above this level ('medium' → medium/high/critical)
            status:    Filter by status ('open', 'reviewed', 'dismissed', 'escalated')
            limit:     Max rows to return

        Returns:
            List of flag dicts ordered by last_detected_at DESC
        """
        _level_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        min_level_int = _level_order.get(min_level or "low", 1)

        conditions = [
            "CASE risk_level "
            "WHEN 'low' THEN 1 WHEN 'medium' THEN 2 "
            "WHEN 'high' THEN 3 WHEN 'critical' THEN 4 ELSE 0 END >= $1"
        ]
        params: List[Any] = [min_level_int]

        if flag_type:
            params.append(flag_type)
            conditions.append(f"flag_type = ${len(params)}")

        if status:
            params.append(status)
            conditions.append(f"status = ${len(params)}")

        params.append(limit)
        limit_idx = len(params)
        params.append(offset)
        offset_idx = len(params)
        where = " AND ".join(conditions)
        query = f"""
        SELECT id, flag_type, fingerprint, account_ids, risk_level, risk_score,
               explanation, details, status, first_detected_at, last_detected_at,
               detection_count, created_at
        FROM risk_flags
        WHERE {where}
        ORDER BY last_detected_at DESC, id DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
        """

        async with self._get_connection() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    @staticmethod
    def _flag_conditions(
        flag_type: Optional[str], min_level: Optional[str], status: Optional[str]
    ) -> Tuple[str, List[Any]]:
        """Shared WHERE clause for the list/count pair (same filters, same order)."""
        _level_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        conditions = [
            "CASE risk_level "
            "WHEN 'low' THEN 1 WHEN 'medium' THEN 2 "
            "WHEN 'high' THEN 3 WHEN 'critical' THEN 4 ELSE 0 END >= $1"
        ]
        params: List[Any] = [_level_order.get(min_level or "low", 1)]
        if flag_type:
            params.append(flag_type)
            conditions.append(f"flag_type = ${len(params)}")
        if status:
            params.append(status)
            conditions.append(f"status = ${len(params)}")
        return " AND ".join(conditions), params

    async def count_risk_flags(
        self,
        flag_type: Optional[str] = None,
        min_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        where, params = self._flag_conditions(flag_type, min_level, status)
        async with self._get_connection() as conn:
            return int(await conn.fetchval(f"SELECT count(*) FROM risk_flags WHERE {where}", *params))

    async def update_risk_flag_status(self, flag_id: int, status: str) -> Optional[Dict[str, Any]]:
        """Analyst workflow: open → reviewed / dismissed / escalated. Returns the
        updated row, or None when no flag has that id."""
        query = """
        UPDATE risk_flags SET status = $2 WHERE id = $1
        RETURNING id, flag_type, fingerprint, account_ids, risk_level, risk_score,
                  explanation, details, status, first_detected_at, last_detected_at,
                  detection_count, created_at
        """
        async with self._get_connection() as conn:
            row = await conn.fetchrow(query, flag_id, status)
        return dict(row) if row else None

    async def count_open_flags_by_type(self) -> Dict[str, int]:
        async with self._get_connection() as conn:
            rows = await conn.fetch(
                "SELECT flag_type, count(*) AS n FROM risk_flags WHERE status = 'open' GROUP BY flag_type"
            )
        return {r["flag_type"]: int(r["n"]) for r in rows}

    async def get_account_ids_for_flag_type(self, flag_type: str, status: str = "open") -> List[str]:
        """Distinct account ids carrying an open flag of one detector type (the
        stats cache uses the AGGREGATE set to mark transactions as flagged)."""
        query = """
        SELECT DISTINCT unnest(account_ids) AS account_id
        FROM risk_flags WHERE flag_type = $1 AND status = $2 ORDER BY account_id
        """
        async with self._get_connection() as conn:
            rows = await conn.fetch(query, flag_type, status)
        return [r["account_id"] for r in rows]

    async def clear_risk_flags(self) -> int:
        """Drop every flag — only for a dataset replacement, where the graph the
        flags describe is gone. Returns the number of rows removed."""
        async with self._get_connection() as conn:
            n = await conn.fetchval("SELECT count(*) FROM risk_flags")
            await conn.execute("DELETE FROM risk_flags")
        return int(n)

    # ==================== APP META (key/value) ====================

    async def ensure_transaction_tables(self) -> None:
        """Apply migration 001 — the `transactions` and `outbox` tables.

        Self-applying and idempotent, matching how the detectors bring up
        `risk_flags`. Without this nothing creates these two tables, so the
        consumer's write path fails on a fresh database.
        """
        sql = (Path(__file__).resolve().parent.parent / "migrations"
               / "001_create_outbox_table.sql").read_text()
        async with self._get_connection() as conn:
            await conn.execute(sql)

    async def ensure_app_meta_table(self) -> None:
        """Apply migration 004 (idempotent) — same self-applying convention the
        detectors use for 002."""
        sql = (Path(__file__).resolve().parent.parent / "migrations"
               / "004_create_app_meta_table.sql").read_text()
        async with self._get_connection() as conn:
            await conn.execute(sql)

    async def get_app_meta(self, key: str) -> Optional[Dict[str, Any]]:
        async with self._get_connection() as conn:
            raw = await conn.fetchval("SELECT value FROM app_meta WHERE key = $1", key)
        if raw is None:
            return None
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    async def set_app_meta(self, key: str, value: Dict[str, Any]) -> None:
        async with self._get_connection() as conn:
            await conn.execute(
                "INSERT INTO app_meta (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                key, json.dumps(value),
            )

    async def get_flagged_account_ids(
        self,
        status: str = "open",
        exclude_flag_type: Optional[str] = None,
    ) -> List[str]:
        """
        Distinct account IDs appearing in risk_flags with the given status.

        exclude_flag_type exists so a detector can measure corroboration from
        OTHER detectors without feeding on its own output: the community scorer
        passes exclude_flag_type='COMMUNITY', otherwise yesterday's community
        flag would inflate today's overlap score in a feedback loop.

        Args:
            status:            Flag status to include ('open' by default)
            exclude_flag_type: Skip flags of this detector type, or None for all

        Returns:
            Sorted list of distinct account IDs
        """
        if exclude_flag_type:
            query = """
            SELECT DISTINCT unnest(account_ids) AS account_id
            FROM risk_flags
            WHERE status = $1 AND flag_type <> $2
            ORDER BY account_id
            """
            args = (status, exclude_flag_type)
        else:
            query = """
            SELECT DISTINCT unnest(account_ids) AS account_id
            FROM risk_flags
            WHERE status = $1
            ORDER BY account_id
            """
            args = (status,)

        async with self._get_connection() as conn:
            rows = await conn.fetch(query, *args)
        return [row["account_id"] for row in rows]

    # ==================== PIPELINE RUNS (community visualiser) ====================

    async def create_pipeline_run(self) -> str:
        """Create a new pipeline run in 'queued' status; returns its uuid string."""
        run_id = str(uuid.uuid4())
        async with self._get_connection() as conn:
            await conn.execute(
                "INSERT INTO pipeline_runs (id, status) VALUES ($1::uuid, 'queued')",
                run_id,
            )
        return run_id

    async def update_pipeline_run(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress: Optional[float] = None,
        counts: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        finished: bool = False,
    ) -> None:
        """Patch a run's mutable fields. Only the provided fields are updated."""
        sets: List[str] = []
        args: List[Any] = []

        def add(col: str, val: Any) -> None:
            args.append(val)
            sets.append(f"{col} = ${len(args)}")

        if status is not None:
            add("status", status)
        if stage is not None:
            add("stage", stage)
        if progress is not None:
            add("progress", progress)
        if counts is not None:
            add("counts", json.dumps(counts))
        if error is not None:
            add("error", error)
        if finished:
            sets.append("finished_at = now()")
        if not sets:
            return
        args.append(run_id)
        query = (
            f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id = ${len(args)}::uuid"
        )
        async with self._get_connection() as conn:
            await conn.execute(query, *args)

    @staticmethod
    def _row_to_run(row: Optional[Any]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        if isinstance(d.get("counts"), str):
            d["counts"] = json.loads(d["counts"])
        d["id"] = str(d["id"])
        return d

    async def get_pipeline_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with self._get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pipeline_runs WHERE id = $1::uuid", run_id
            )
        return self._row_to_run(row)

    async def get_latest_pipeline_run(self) -> Optional[Dict[str, Any]]:
        async with self._get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
            )
        return self._row_to_run(row)

    async def get_active_pipeline_run(self) -> Optional[Dict[str, Any]]:
        async with self._get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pipeline_runs WHERE status IN ('queued','running') "
                "ORDER BY started_at DESC LIMIT 1"
            )
        return self._row_to_run(row)

    async def health_check(self) -> bool:
        """Test database connection."""
        try:
            async with self._get_connection() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")
            return False
