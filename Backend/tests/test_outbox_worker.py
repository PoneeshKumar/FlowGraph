"""
Tests for outbox sync worker.
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from worker.outbox_sync_worker import OutboxSyncWorker


class TestOutboxSyncWorker:
    """Tests for OutboxSyncWorker."""

    @pytest.fixture
    def outbox_worker(
        self, mock_postgres_client, mock_neo4j_client, mock_redis_client
    ):
        """Create an OutboxSyncWorker instance."""
        return OutboxSyncWorker(
            postgres_client=mock_postgres_client,
            neo4j_client=mock_neo4j_client,
            redis_client=mock_redis_client,
        )

    def test_initialization(self, outbox_worker):
        """Test worker initializes correctly."""
        assert outbox_worker.postgres is not None
        assert outbox_worker.neo4j is not None
        assert outbox_worker.redis is not None
        assert outbox_worker.is_running is False

    @pytest.mark.asyncio
    async def test_sync_cycle_no_pending_records(
        self, outbox_worker, mock_postgres_client
    ):
        """Test sync cycle when no pending records."""
        mock_postgres_client.fetch_pending_outbox.return_value = []
        
        await outbox_worker._sync_cycle()
        
        # Should fetch pending but find none
        mock_postgres_client.fetch_pending_outbox.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_cycle_successful_sync(
        self, outbox_worker, mock_postgres_client, mock_neo4j_client, mock_redis_client
    ):
        """Test successful sync of pending record."""
        pending_record = {
            "id": 1,
            "transaction_id": str(uuid4()),
            "idempotency_key": "key123",
            "event_payload": {
                "event_id": str(uuid4()),
                "sender_id": "sender_123",
                "receiver_id": "receiver_456",
                "amount_cents": 5000,
                "rail": "CARD",
                "event_type": "AUTH",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            },
            "retry_count": 0,
            "last_retry_at": None,
        }
        
        mock_postgres_client.fetch_pending_outbox.return_value = [pending_record]
        mock_neo4j_client.upsert_transaction_graph = AsyncMock()
        mock_redis_client.add_edge_to_timeseries = AsyncMock()
        
        await outbox_worker._sync_cycle()
        
        # Verify Neo4j and Redis were called
        mock_neo4j_client.upsert_transaction_graph.assert_called_once()
        mock_redis_client.add_edge_to_timeseries.assert_called_once()
        
        # Verify marked as synced
        mock_postgres_client.mark_outbox_synced.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_sync_cycle_does_not_recompute_pagerank_after_upsert(
        self, outbox_worker, mock_postgres_client, mock_neo4j_client, mock_redis_client
    ):
        """PageRank should be computed once via the graph upsert path, not again in the worker."""
        pending_record = {
            "id": 1,
            "transaction_id": str(uuid4()),
            "idempotency_key": "key123",
            "event_payload": {
                "event_id": str(uuid4()),
                "sender_id": "sender_123",
                "receiver_id": "receiver_456",
                "amount_cents": 5000,
                "rail": "CARD",
                "event_type": "AUTH",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            },
            "retry_count": 0,
            "last_retry_at": None,
        }

        mock_postgres_client.fetch_pending_outbox.return_value = [pending_record]
        mock_neo4j_client.upsert_transaction_graph = AsyncMock()
        mock_neo4j_client.compute_local_pagerank = AsyncMock()
        mock_redis_client.add_edge_to_timeseries = AsyncMock()

        await outbox_worker._sync_cycle()

        mock_neo4j_client.upsert_transaction_graph.assert_called_once()
        mock_neo4j_client.compute_local_pagerank.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_cycle_neo4j_failure_on_first_attempt(
        self, outbox_worker, mock_postgres_client, mock_neo4j_client
    ):
        """Test retry on Neo4j failure."""
        pending_record = {
            "id": 1,
            "transaction_id": str(uuid4()),
            "idempotency_key": "key123",
            "event_payload": {
                "event_id": str(uuid4()),
                "sender_id": "sender_123",
                "receiver_id": "receiver_456",
                "amount_cents": 5000,
                "rail": "CARD",
                "event_type": "AUTH",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            },
            "retry_count": 0,
            "last_retry_at": None,
        }
        
        mock_postgres_client.fetch_pending_outbox.return_value = [pending_record]
        
        # Simulate Neo4j failure
        mock_neo4j_client.upsert_transaction_graph = AsyncMock(
            side_effect=Exception("Neo4j connection error")
        )
        
        await outbox_worker._sync_cycle()
        
        # Should increment retry count instead of marking synced
        mock_postgres_client.increment_outbox_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_cycle_max_retries_exceeded(
        self, outbox_worker, mock_postgres_client, mock_neo4j_client
    ):
        """Test marking as failed when max retries exceeded."""
        pending_record = {
            "id": 1,
            "transaction_id": str(uuid4()),
            "idempotency_key": "key123",
            "event_payload": {
                "event_id": str(uuid4()),
                "sender_id": "sender_123",
                "receiver_id": "receiver_456",
                "amount_cents": 5000,
                "rail": "CARD",
                "event_type": "AUTH",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            },
            "retry_count": 3,  # Already at max
            "last_retry_at": None,
        }
        
        mock_postgres_client.fetch_pending_outbox.return_value = [pending_record]
        
        # Simulate Neo4j failure
        mock_neo4j_client.upsert_transaction_graph = AsyncMock(
            side_effect=Exception("Neo4j error")
        )
        
        await outbox_worker._sync_cycle()
        
        # Should mark as failed
        mock_postgres_client.mark_outbox_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_retry_first_attempt(self, outbox_worker):
        """Test that first attempt (retry_count=0) is always retried."""
        record = {
            "id": 1,
            "retry_count": 0,
            "last_retry_at": None,
        }
        
        should_retry = await outbox_worker._should_retry(record)
        
        assert should_retry is True

    @pytest.mark.asyncio
    async def test_should_retry_not_ready_yet(self, outbox_worker):
        """Test that record is not retried if backoff delay hasn't passed."""
        now = datetime.utcnow()
        recent_time = now - timedelta(seconds=5)  # Only 5 seconds ago
        
        record = {
            "id": 1,
            "retry_count": 2,
            "last_retry_at": recent_time,
        }
        
        with patch("worker.outbox_sync_worker.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value = now
            should_retry = await outbox_worker._should_retry(record)
        
        # Retry interval is 10 sec, so 5 sec is not enough
        assert should_retry is False

    @pytest.mark.asyncio
    async def test_should_retry_backoff_expired(self, outbox_worker):
        """Test that record is retried after backoff delay passes."""
        now = datetime.utcnow()
        old_time = now - timedelta(seconds=30)  # 30 seconds ago
        
        record = {
            "id": 1,
            "retry_count": 2,  # 2 * 10 = 20 sec backoff
            "last_retry_at": old_time,
        }
        
        with patch("worker.outbox_sync_worker.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value = now
            should_retry = await outbox_worker._should_retry(record)
        
        # 30 sec > 20 sec backoff, so should retry
        assert should_retry is True

    @pytest.mark.asyncio
    async def test_stop_worker(self, outbox_worker):
        """Test stopping the worker."""
        outbox_worker.is_running = True
        
        await outbox_worker.stop()
        
        assert outbox_worker.is_running is False
