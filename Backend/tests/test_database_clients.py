"""
Tests for database clients.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import json

from db.neo4j import Neo4jClient

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
    async def test_compute_local_pagerank_without_driver(self):
        """Test PageRank computation returns empty without an initialized driver."""
        client = Neo4jClient()

        result = await client.compute_local_pagerank(["account_123", "account_456"])

        assert result == {}

    @pytest.mark.asyncio
    async def test_export_account_nodes_returns_gnn_feature_properties(self):
        """The node half of the GNN feature export.

        Checks the query covers all four schema node types and that rows are
        parsed into the shape ml.features expects.
        """
        rows = [
            {
                "id": "acct_a",
                "labels": ["Account"],
                "pagerank_score": 0.25,
                "community_id": "ab12cd34ef56",
                "created_at": 1_785_000_000_000,
                "kyc_tier": None,
                "risk_score": None,
                "country": None,
                "account_age": None,
                "cumulative_volume": None,
            },
            # No id — a malformed node must be filtered out, not emitted.
            {
                "id": None,
                "labels": ["Account"],
                "pagerank_score": None,
                "community_id": None,
                "created_at": None,
                "kyc_tier": None,
                "risk_score": None,
                "country": None,
                "account_age": None,
                "cumulative_volume": None,
            },
        ]

        class FakeResult:
            def __init__(self, records):
                self._records = records

            def __aiter__(self):
                async def gen():
                    for record in self._records:
                        yield record

                return gen()

        class FakeSession:
            def __init__(self, queries):
                self.queries = queries

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def run(self, query, **kwargs):
                self.queries.append(query)
                return FakeResult(rows)

        class FakeDriver:
            def __init__(self, session):
                self._session = session

            def session(self, database=None):
                return self._session

        queries = []
        client = Neo4jClient()
        client.driver = FakeDriver(FakeSession(queries))

        result = await client.export_account_nodes()

        assert len(result) == 1
        assert result[0]["id"] == "acct_a"
        assert result[0]["labels"] == ["Account"]
        assert result[0]["pagerank_score"] == 0.25
        assert result[0]["community_id"] == "ab12cd34ef56"
        # Properties nothing populates yet still come back, as None.
        assert result[0]["kyc_tier"] is None

        text = queries[0].text if hasattr(queries[0], "text") else str(queries[0])
        for node_type in ("Account", "Merchant", "Bank", "Exchange"):
            assert node_type in text

    @pytest.mark.asyncio
    async def test_compute_local_pagerank_uses_flows_to_edges(self):
        """PageRank should read the aggregated FLOWS_TO edge weights."""
        client = Neo4jClient()

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows

            async def data(self):
                return self._rows

        class FakeSession:
            def __init__(self, query_calls):
                self.query_calls = query_calls

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def run(self, query, **kwargs):
                self.query_calls.append((query, kwargs))
                return FakeResult([])

        class FakeDriver:
            def __init__(self, session):
                self._session = session

            def session(self, database=None):
                return self._session

        query_calls = []
        client.driver = FakeDriver(FakeSession(query_calls))

        result = await client.compute_local_pagerank(["acct_a", "acct_b"])

        # No edges came back, but the requested accounts are still seeded into
        # the adjacency map, so PageRank distributes mass uniformly over them
        # rather than returning nothing. The old expectation of {} predated
        # that seeding.
        assert set(result) == {"acct_a", "acct_b"}
        assert sum(result.values()) == pytest.approx(1.0, abs=1e-6)
        assert query_calls, "PageRank query should be executed"
        query, kwargs = query_calls[0]
        assert "FLOWS_TO" in query
        assert "rel.total_amount" in query
        assert "rel.amount_cents" not in query

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
        """find_cycles returns a list of {node_ids, amounts, timestamps, txn_ids} dicts."""
        mock_neo4j_client.find_cycles.return_value = []

        # New signature: keyword args for all detection parameters
        cycles = await mock_neo4j_client.find_cycles(
            "account_123",
            max_depth=6,
            window_hours=48,
        )

        assert isinstance(cycles, list)

    @pytest.mark.asyncio
    async def test_find_cycles_result_shape(self, mock_neo4j_client):
        """Non-empty result contains the expected keys."""
        from datetime import datetime, timezone
        now = int(datetime.now(timezone.utc).timestamp())
        mock_neo4j_client.find_cycles.return_value = [
            {
                "node_ids":   ["A", "B", "C", "A"],
                "amounts":    [5_000_000, 4_750_000, 4_500_000],
                "timestamps": [now, now + 3600, now + 7200],
                "txn_ids":    ["txn-1", "txn-2", "txn-3"],
            }
        ]

        result = await mock_neo4j_client.find_cycles("A")
        assert len(result) == 1
        cycle = result[0]
        for key in ("node_ids", "amounts", "timestamps", "txn_ids"):
            assert key in cycle, f"Missing key in cycle result: {key}"

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
