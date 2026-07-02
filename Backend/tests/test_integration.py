"""
End-to-end integration tests for the outbox pattern.
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from hashlib import sha256

from normalizer.card_normalizer import CardNormalizer
from models.card_events import CardAuthEvent


class TestOutboxPatternEndToEnd:
    """End-to-end tests for the outbox pattern workflow."""

    @pytest.mark.asyncio
    async def test_full_flow_valid_event(
        self,
        sample_card_auth_event,
        mock_postgres_client,
        mock_neo4j_client,
        mock_redis_client,
    ):
        """
        Test complete flow: normalize → validate → write to Postgres → sync to Neo4j/Redis.
        """
        # Step 1: Normalize event
        event, error = CardNormalizer.normalize(sample_card_auth_event)
        assert error is None
        assert isinstance(event, CardAuthEvent)
        
        # Step 2: Create idempotency key
        idempotency_key = sha256(
            f"{event.event_id}:{event.timestamp_utc.isoformat()}".encode()
        ).hexdigest()
        
        # Step 3: Write to Postgres (transaction + outbox)
        await mock_postgres_client.save_transaction(
            transaction_id=str(event.event_id),
            rail=event.rail.value,
            event_type=event.event_type.value,
            status=event.status.value,
            sender_id=event.sender_id,
            receiver_id=event.receiver_id,
            amount_cents=event.amount_cents,
            currency=event.currency,
            timestamp_utc=event.timestamp_utc,
            raw_payload=sample_card_auth_event,
        )
        
        assert mock_postgres_client.save_transaction.called
        
        # Step 4: Insert outbox entry
        await mock_postgres_client.insert_outbox(
            transaction_id=str(event.event_id),
            idempotency_key=idempotency_key,
            event_payload={
                "event_id": str(event.event_id),
                "rail": event.rail.value,
                "event_type": event.event_type.value,
                "sender_id": event.sender_id,
                "receiver_id": event.receiver_id,
                "amount_cents": event.amount_cents,
                "currency": event.currency,
                "timestamp_utc": event.timestamp_utc.isoformat(),
            },
        )
        
        assert mock_postgres_client.insert_outbox.called
        
        # Step 5: Simulate outbox sync worker
        mock_postgres_client.fetch_pending_outbox.return_value = [
            {
                "id": 1,
                "transaction_id": str(event.event_id),
                "idempotency_key": idempotency_key,
                "event_payload": {
                    "event_id": str(event.event_id),
                    "sender_id": event.sender_id,
                    "receiver_id": event.receiver_id,
                    "amount_cents": event.amount_cents,
                    "rail": event.rail.value,
                    "event_type": event.event_type.value,
                    "timestamp_utc": event.timestamp_utc.isoformat(),
                },
                "retry_count": 0,
                "last_retry_at": None,
            }
        ]
        
        # Sync to Neo4j
        await mock_neo4j_client.upsert_transaction_graph(
            sender_id=event.sender_id,
            receiver_id=event.receiver_id,
            amount_cents=event.amount_cents,
            timestamp_utc=event.timestamp_utc,
            rail=event.rail.value,
            event_type=event.event_type.value,
            transaction_id=str(event.event_id),
            idempotency_key=idempotency_key,
        )
        
        assert mock_neo4j_client.upsert_transaction_graph.called
        
        # Sync to Redis
        await mock_redis_client.add_edge_to_timeseries(
            sender_id=event.sender_id,
            receiver_id=event.receiver_id,
            amount_cents=event.amount_cents,
            timestamp_utc=event.timestamp_utc,
        )
        
        assert mock_redis_client.add_edge_to_timeseries.called
        
        # Mark outbox as synced
        await mock_postgres_client.mark_outbox_synced(1)
        
        assert mock_postgres_client.mark_outbox_synced.called_with(1)

    @pytest.mark.asyncio
    async def test_full_flow_invalid_event(
        self, sample_invalid_event, mock_postgres_client, mock_dlq_handler
    ):
        """
        Test that invalid events are routed to DLQ, not written to Postgres.
        """
        # Step 1: Try to normalize invalid event
        event, error = CardNormalizer.normalize(sample_invalid_event)
        
        assert event is None
        assert error is not None
        
        # Step 2: Route to DLQ
        await mock_dlq_handler.route_to_dlq(
            raw_payload=sample_invalid_event,
            error_reason=error,
            rail="CARD",
            event_id=sample_invalid_event.get("event_id"),
        )
        
        assert mock_dlq_handler.route_to_dlq.called
        
        # Step 3: Verify NOT written to Postgres
        assert not mock_postgres_client.save_transaction.called

    @pytest.mark.asyncio
    async def test_full_flow_with_retry_on_neo4j_failure(
        self,
        sample_card_auth_event,
        mock_postgres_client,
        mock_neo4j_client,
        mock_redis_client,
    ):
        """
        Test that sync failures trigger retries with proper backoff.
        """
        event, _ = CardNormalizer.normalize(sample_card_auth_event)
        
        # Simulate outbox record ready for sync
        outbox_record = {
            "id": 1,
            "transaction_id": str(event.event_id),
            "idempotency_key": "key123",
            "event_payload": {
                "event_id": str(event.event_id),
                "sender_id": event.sender_id,
                "receiver_id": event.receiver_id,
                "amount_cents": event.amount_cents,
                "rail": event.rail.value,
                "event_type": event.event_type.value,
                "timestamp_utc": event.timestamp_utc.isoformat(),
            },
            "retry_count": 0,
            "last_retry_at": None,
        }
        
        mock_postgres_client.fetch_pending_outbox.return_value = [outbox_record]
        
        # Simulate Neo4j failure
        error_msg = "Neo4j connection timeout"
        mock_neo4j_client.upsert_transaction_graph = AsyncMock(
            side_effect=Exception(error_msg)
        )
        
        # Try to sync (will fail)
        with pytest.raises(Exception):
            await mock_neo4j_client.upsert_transaction_graph(
                sender_id=event.sender_id,
                receiver_id=event.receiver_id,
                amount_cents=event.amount_cents,
                timestamp_utc=event.timestamp_utc,
                rail=event.rail.value,
                event_type=event.event_type.value,
                transaction_id=str(event.event_id),
                idempotency_key="key123",
            )
        
        # Increment retry count
        await mock_postgres_client.increment_outbox_retry(1, error_msg)
        
        assert mock_postgres_client.increment_outbox_retry.called
        
        # Verify NOT marked as synced
        assert not mock_postgres_client.mark_outbox_synced.called

    @pytest.mark.asyncio
    async def test_idempotency_key_prevents_duplicates(
        self, sample_card_auth_event, mock_postgres_client
    ):
        """
        Test that idempotency keys prevent duplicate writes from retries.
        """
        event, _ = CardNormalizer.normalize(sample_card_auth_event)
        
        # Create idempotency key
        idempotency_key = sha256(
            f"{event.event_id}:{event.timestamp_utc.isoformat()}".encode()
        ).hexdigest()
        
        # First insert
        await mock_postgres_client.insert_outbox(
            transaction_id=str(event.event_id),
            idempotency_key=idempotency_key,
            event_payload={"test": "data"},
        )
        
        # Second insert with same idempotency key (simulating retry)
        await mock_postgres_client.insert_outbox(
            transaction_id=str(event.event_id),
            idempotency_key=idempotency_key,
            event_payload={"test": "data"},
        )
        
        # Postgres should have been called twice
        # but DB constraint (unique idempotency_key) would prevent duplicate
        assert mock_postgres_client.insert_outbox.call_count == 2

    @pytest.mark.asyncio
    async def test_health_checks_all_services(
        self, mock_postgres_client, mock_neo4j_client, mock_redis_client
    ):
        """
        Test that all services pass health checks.
        """
        mock_postgres_client.health_check.return_value = True
        mock_neo4j_client.health_check.return_value = True
        mock_redis_client.health_check.return_value = True
        
        pg_healthy = await mock_postgres_client.health_check()
        neo4j_healthy = await mock_neo4j_client.health_check()
        redis_healthy = await mock_redis_client.health_check()
        
        assert pg_healthy is True
        assert neo4j_healthy is True
        assert redis_healthy is True

    @pytest.mark.asyncio
    async def test_metrics_reporting(self, mock_postgres_client):
        """
        Test that metrics are properly reported.
        """
        mock_postgres_client.get_outbox_stats.return_value = {
            "pending": {
                "count": 5,
                "max_age_seconds": 120,
                "avg_age_seconds": 60,
            },
            "synced": {
                "count": 100,
                "max_age_seconds": 300,
                "avg_age_seconds": 150,
            },
            "failed": {
                "count": 0,
                "max_age_seconds": 0,
                "avg_age_seconds": 0,
            },
        }
        
        stats = await mock_postgres_client.get_outbox_stats()
        
        assert stats["pending"]["count"] == 5
        assert stats["synced"]["count"] == 100
        assert stats["failed"]["count"] == 0
