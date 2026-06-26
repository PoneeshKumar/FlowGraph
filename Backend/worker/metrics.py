"""
Metrics reporter for FlowGraph outbox worker.

Tracks sync statistics: pending count, synced/failed/retrying rates,
max pending age, average sync latency.
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from config import METRICS_REPORT_INTERVAL_SECONDS, LOG_LEVEL

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)


class MetricsReporter:
    """Tracks and reports outbox sync metrics."""

    def __init__(self, postgres_client: Any):
        """
        Initialize metrics reporter.
        
        Args:
            postgres_client: PostgresClient instance for fetching stats
        """
        self.postgres = postgres_client
        self.is_running = False
        
        # Cumulative counters (persisted across reports)
        self.total_synced = 0
        self.total_failed = 0
        self.total_retried = 0

    async def report_loop(self) -> None:
        """
        Main metrics loop: periodically fetch and report statistics.
        """
        self.is_running = True
        logger.info(
            f"Metrics reporter started | "
            f"interval={METRICS_REPORT_INTERVAL_SECONDS}s"
        )

        while self.is_running:
            try:
                await self._report_cycle()
            except Exception as e:
                logger.error(f"Error in metrics cycle: {e}", exc_info=True)
            
            await asyncio.sleep(METRICS_REPORT_INTERVAL_SECONDS)

    async def _report_cycle(self) -> None:
        """
        Single metrics cycle: fetch stats from Postgres and log.
        """
        try:
            stats = await self.postgres.get_outbox_stats()
            
            # Extract status-specific counts
            pending = stats.get("pending", {})
            synced = stats.get("synced", {})
            failed = stats.get("failed", {})
            
            pending_count = pending.get("count", 0)
            synced_count = synced.get("count", 0)
            failed_count = failed.get("count", 0)
            
            # Calculate ages
            pending_max_age_s = pending.get("max_age_seconds") or 0
            pending_avg_age_s = pending.get("avg_age_seconds") or 0
            
            # Format log message
            log_msg = (
                f"Outbox Metrics | "
                f"pending={pending_count} (max_age={pending_max_age_s:.0f}s, avg={pending_avg_age_s:.0f}s) | "
                f"synced={synced_count} | "
                f"failed={failed_count} | "
                f"cumulative_synced={self.total_synced} | "
                f"cumulative_failed={self.total_failed}"
            )
            
            logger.info(log_msg)
            
            # Alert if too many pending
            if pending_count > 1000:
                logger.warning(
                    f"HIGH pending outbox records: {pending_count} | "
                    f"max_age={pending_max_age_s:.0f}s | "
                    f"Consider scaling sync worker"
                )
            
            # Alert if sync failures accumulating
            if failed_count > 100:
                logger.error(
                    f"HIGH failed outbox records: {failed_count} | "
                    f"Investigate Neo4j/Redis connectivity"
                )
        
        except Exception as e:
            logger.error(f"Failed to fetch outbox stats: {e}", exc_info=True)

    async def record_sync_cycle(
        self,
        synced: int = 0,
        failed: int = 0,
        retried: int = 0,
    ) -> None:
        """
        Record results from a sync cycle.
        
        Args:
            synced: Count of successfully synced records
            failed: Count of records that failed after max retries
            retried: Count of records queued for retry
        """
        self.total_synced += synced
        self.total_failed += failed
        self.total_retried += retried

    async def stop(self) -> None:
        """Stop the metrics loop."""
        self.is_running = False
        logger.info("Metrics reporter stopped")
