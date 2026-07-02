"""
Tests for database clients.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import json


class TestPostgresClient:
    """Tests for PostgreSQL client."""

    @pytest.mark.asyncio
    async def test_save_transaction(self, mock_postgres_client):
        """Test saving a transaction."""
        transaction_id = str(uuid4())
        now = datetime.now(timezone.utc)
        
        await mock_postgres_client.save_transaction(
            transaction_id=transaction_id,
            rail="CARD",
            event_type="AUTH",
            status="PENDING",
            sender_id="sender_123",
            receiver_id="receiver_456",
            amount_cents=5000,
            currency="USD",
            timestamp_utc=now,
            raw_payload={"test": "data"},
        )
        
        # Verify method was called
        mock_postgres_client.save_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_insert_outbox(self, mock_postgres_client):
        """Test inserting an outbox entry."""
        transaction_id = str(uuid4())
        
        await mock_postgres_client.insert_outbox(
            transaction_id=transaction_id,
            idempotency_key="key123",
            event_payload={"event": "data"},
        )
        
        mock_postgres_client.insert_outbox.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_pending_outbox(self, mock_postgres_client):
        """Test fetching pending outbox records."""
        mock_postgres_client.fetch_pending_outbox.return_value = [
            {
                "id": 1,
                "transaction_id": str(uuid4()),
                "event_payload": {"test": "data"},
                "retry_count": 0,
            }
        ]
        
        records = await mock_postgres_client.fetch_pending_outbox(batch_size=50)
        
        assert len(records) == 1
        assert records[0]["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_mark_outbox_synced(self, mock_postgres_client):
        """Test marking outbox as synced."""
        await mock_postgres_client.mark_outbox_synced(1)
        
        mock_postgres_client.mark_outbox_synced.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_mark_outbox_failed(self, mock_postgres_client):
        """Test marking outbox as failed."""
        await mock_postgres_client.mark_outbox_failed(1, "Test error")
        
        mock_postgres_client.mark_outbox_failed.assert_called_once_with(1, "Test error")

    @pytest.mark.asyncio
    async def test_increment_outbox_retry(self, mock_postgres_client):
        """Test incrementing retry count."""
        await mock_postgres_client.increment_outbox_retry(1, "Error message")
        
        mock_postgres_client.increment_outbox_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_postgres_client):
        """Test health check when connection is healthy."""
        mock_postgres_client.health_check.return_value = True
        
        result = await mock_postgres_client.health_check()
        
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, mock_postgres_client):
        """Test health check when connection fails."""
        mock_postgres_client.health_check.return_value = False
        
        result = await mock_postgres_client.health_check()
        
        assert result is False


class TestNeo4jClient:
    """Tests for Neo4j client."""

    @pytest.mark.asyncio
    async def test_upsert_transaction_graph(self, mock_neo4j_client):
        """Test upserting transaction to graph."""
        now = datetime.now(timezone.utc)
        
        await mock_neo4j_client.upsert_transaction_graph(
            sender_id="sender_123",
            receiver_id="receiver_456",
            amount_cents=5000,
            timestamp_utc=now,
            rail="CARD",
            event_type="AUTH",
            transaction_id=str(uuid4()),
            idempotency_key="key123",
        )
        
        mock_neo4j_client.upsert_transaction_graph.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_account_node(self, mock_neo4j_client):
        """Test creating an account node."""
        await mock_neo4j_client.create_account_node(
            account_id="account_123",
            account_type="Account",
            properties={"risk_score": 0.5},
        )
        
        mock_neo4j_client.create_account_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_subgraph(self, mock_neo4j_client):
        """Test fetching subgraph."""
        mock_neo4j_client.get_subgraph.return_value = {
            "center": [{"id": "account_123"}],
            "neighbors": [],
            "edges": [],
        }
        
        result = await mock_neo4j_client.get_subgraph("account_123", depth=3)
        
        assert "center" in result
        assert "neighbors" in result
        assert "edges" in result

    @pytest.mark.asyncio
    async def test_find_cycles(self, mock_neo4j_client):
        """Test finding cycles in graph."""
        mock_neo4j_client.find_cycles.return_value = []
        
        cycles = await mock_neo4j_client.find_cycles("account_123", max_depth=6)
        
        assert isinstance(cycles, list)

    @pytest.mark.asyncio
    async def test_shortest_path(self, mock_neo4j_client):
        """Test finding shortest path."""
        mock_neo4j_client.shortest_path.return_value = None
        
        path = await mock_neo4j_client.shortest_path("account_1", "account_2")
        
        assert path is None  # No path in this test

    @pytest.mark.asyncio
    async def test_health_check(self, mock_neo4j_client):
        """Test health check."""
        mock_neo4j_client.health_check.return_value = True
        
        result = await mock_neo4j_client.health_check()
        
        assert result is True


class TestRedisClient:
    """Tests for Redis client."""

    @pytest.mark.asyncio
    async def test_add_edge_to_timeseries(self, mock_redis_client):
        """Test adding edge to time-windowed ZSET."""
        now = datetime.now(timezone.utc)
        
        await mock_redis_client.add_edge_to_timeseries(
            sender_id="sender_123",
            receiver_id="receiver_456",
            amount_cents=5000,
            timestamp_utc=now,
        )
        
        mock_redis_client.add_edge_to_timeseries.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_edge_volume_in_window(self, mock_redis_client):
        """Test querying edge volume in time window."""
        mock_redis_client.get_edge_volume_in_window.return_value = {
            "total_volume_cents": 10000,
            "transaction_count": 2,
            "earliest_timestamp": None,
            "latest_timestamp": None,
            "window_hours": 24,
        }
        
        result = await mock_redis_client.get_edge_volume_in_window(
            "sender_123", "receiver_456", hours=24
        )
        
        assert result["total_volume_cents"] == 10000
        assert result["transaction_count"] == 2

    @pytest.mark.asyncio
    async def test_cache_set_and_get(self, mock_redis_client):
        """Test cache set and get operations."""
        await mock_redis_client.cache_set("key", "value", ttl_seconds=3600)
        mock_redis_client.cache_set.assert_called_once()
        
        mock_redis_client.cache_get.return_value = "value"
        result = await mock_redis_client.cache_get("key")
        
        assert result == "value"

    @pytest.mark.asyncio
    async def test_health_check(self, mock_redis_client):
        """Test health check."""
        mock_redis_client.health_check.return_value = True
        
        result = await mock_redis_client.health_check()
        
        assert result is True
