"""
Outbox sync worker for FlowGraph.

Background process that polls the outbox table and syncs pending records to Neo4j/Redis.
Implements linear retry backoff on failures. Eventually consistent with Postgres.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from config import (
    OUTBOX_POLL_INTERVAL_SECONDS,
    OUTBOX_BATCH_SIZE,
    OUTBOX_MAX_RETRIES,
    OUTBOX_RETRY_INTERVAL_SECONDS,
    LOG_LEVEL,
)

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)


class OutboxSyncWorker:
    """Background worker that syncs outbox entries to Neo4j and Redis."""

    def __init__(
        self,
        postgres_client: Any,
        neo4j_client: Any,
        redis_client: Any,
        metrics_reporter: Optional[Any] = None,
        scorer: Optional[Any] = None,
    ):
        """
        Initialize sync worker.

        Args:
            postgres_client: PostgresClient instance
            neo4j_client: Neo4jClient instance
            redis_client: RedisClient instance
            metrics_reporter: Optional MetricsReporter instance
            scorer: Optional IncrementalScorer. When set, each sync cycle
                re-scores the accounts its synced events touched (best-effort —
                scoring never blocks or fails the sync).
        """
        self.postgres = postgres_client
        self.neo4j = neo4j_client
        self.redis = redis_client
        self.metrics = metrics_reporter
        self.scorer = scorer
        self.is_running = False

    async def sync_loop(self) -> None:
        """
        Main sync loop: continuously poll outbox and sync pending records.
        
        Runs until stopped externally.
        """
        self.is_running = True
        logger.info(
            f"Outbox sync worker started | "
            f"poll_interval={OUTBOX_POLL_INTERVAL_SECONDS}s | "
            f"batch_size={OUTBOX_BATCH_SIZE}"
        )

        while self.is_running:
            try:
                await self._sync_cycle()
            except Exception as e:
                logger.error(
                    f"Error in outbox sync cycle: {e}",
                    exc_info=True,
                )
                # Continue on error — don't crash the worker
            
            # Wait before next poll
            await asyncio.sleep(OUTBOX_POLL_INTERVAL_SECONDS)

    async def _sync_cycle(self) -> None:
        """
        Single sync cycle: fetch pending records and process them.
        """
        try:
            # Fetch batch of pending records
            pending_records = await self.postgres.fetch_pending_outbox(
                batch_size=OUTBOX_BATCH_SIZE
            )

            if not pending_records:
                logger.debug("No pending outbox records to sync")
                return

            logger.info(f"Syncing {len(pending_records)} outbox records")

            # Process each record
            synced_count = 0
            failed_count = 0
            retried_count = 0
            touched: set = set()          # accounts whose edges landed this cycle

            for record in pending_records:
                try:
                    # Check if this record needs a retry delay
                    if not await self._should_retry(record):
                        retried_count += 1
                        continue

                    # Extract sync data
                    event_payload = record["event_payload"]

                    # Sync to Neo4j
                    await self.neo4j.upsert_transaction_graph(
                        sender_id=event_payload["sender_id"],
                        receiver_id=event_payload["receiver_id"],
                        amount_cents=event_payload["amount_cents"],
                        timestamp_utc=datetime.fromisoformat(
                            event_payload["timestamp_utc"]
                        ),
                        rail=event_payload["rail"],
                        event_type=event_payload["event_type"],
                        transaction_id=event_payload["event_id"],
                        idempotency_key=record["idempotency_key"],
                    )

                    # Sync to Redis (time-windowed edge weights)
                    await self.redis.add_edge_to_timeseries(
                        sender_id=event_payload["sender_id"],
                        receiver_id=event_payload["receiver_id"],
                        amount_cents=event_payload["amount_cents"],
                        timestamp_utc=datetime.fromisoformat(
                            event_payload["timestamp_utc"]
                        ),
                    )

                    # Mark as synced
                    await self.postgres.mark_outbox_synced(record["id"])
                    synced_count += 1
                    touched.add(event_payload["sender_id"])
                    touched.add(event_payload["receiver_id"])
                    
                    logger.debug(
                        f"Synced outbox record {record['id']} | "
                        f"{event_payload['sender_id']} → {event_payload['receiver_id']}"
                    )

                except Exception as e:
                    # Sync failed — handle retry
                    error_msg = str(e)
                    logger.warning(
                        f"Sync failed for outbox record {record['id']}: {error_msg}"
                    )

                    if record["retry_count"] >= OUTBOX_MAX_RETRIES:
                        # Max retries exceeded — mark as failed
                        await self.postgres.mark_outbox_failed(
                            record["id"], error_msg
                        )
                        failed_count += 1
                        logger.error(
                            f"Outbox record {record['id']} marked as failed | "
                            f"retries={record['retry_count']}"
                        )
                    else:
                        # Increment retry count
                        await self.postgres.increment_outbox_retry(
                            record["id"], error_msg
                        )
                        retried_count += 1
                        logger.info(
                            f"Outbox record {record['id']} queued for retry | "
                            f"retry_count={record['retry_count'] + 1}"
                        )

            # Report metrics
            if self.metrics:
                await self.metrics.record_sync_cycle(
                    synced=synced_count,
                    failed=failed_count,
                    retried=retried_count,
                )

            # Live per-event GNN re-scoring of the accounts this cycle touched.
            # Best-effort: the graph is already consistent, so a scoring failure
            # must never block or fail the sync (liveness over completeness).
            if self.scorer is not None and touched:
                try:
                    await self.scorer.score_touched(touched)
                except Exception as score_err:
                    logger.warning(
                        f"Live scoring failed for {len(touched)} touched account(s): "
                        f"{score_err}"
                    )

        except Exception as e:
            logger.error(f"Error in sync cycle: {e}", exc_info=True)

    async def _should_retry(self, record: Dict[str, Any]) -> bool:
        """
        Check if a record should be retried based on backoff.
        
        Linear backoff: retry_count * OUTBOX_RETRY_INTERVAL_SECONDS
        
        Args:
            record: Outbox record from Postgres
        
        Returns:
            True if enough time has passed since last retry
        """
        if record["retry_count"] == 0:
            # First attempt — always eligible
            return True

        last_retry = record.get("last_retry_at")
        if not last_retry:
            return True

        # Calculate wait time: retry_count * interval
        wait_seconds = record["retry_count"] * OUTBOX_RETRY_INTERVAL_SECONDS
        next_attempt_time = last_retry + timedelta(seconds=wait_seconds)

        should_retry = datetime.utcnow() >= next_attempt_time
        
        if not should_retry:
            seconds_until_retry = (
                next_attempt_time - datetime.utcnow()
            ).total_seconds()
            logger.debug(
                f"Outbox record {record['id']} not ready for retry | "
                f"will retry in {seconds_until_retry:.1f}s"
            )

        return should_retry

    async def stop(self) -> None:
        """Stop the sync loop gracefully."""
        self.is_running = False
        logger.info("Outbox sync worker stopped")
