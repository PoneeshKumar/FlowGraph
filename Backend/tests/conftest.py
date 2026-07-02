"""
Pytest configuration and shared fixtures for FlowGraph tests.
"""

import pytest
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Generator
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

# Add Backend to path for imports
import sys
from pathlib import Path

BACKEND_PATH = Path(__file__).parent.parent
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_card_auth_event() -> Dict[str, Any]:
    """Sample valid CardAuthEvent payload."""
    event_id = str(uuid4())
    return {
        "event_id": event_id,
        "schema_version": 1,
        "rail": "CARD",
        "event_type": "AUTH",
        "status": "PENDING",
        "sender_id": "cardholder_hash_123",
        "receiver_id": "merchant_hash_456",
        "amount_cents": 5000,
        "currency": "USD",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "raw_payload": {
            "authorization_code": "ABC123",
            "merchant_name": "Starbucks",
            "location": "San Francisco",
        },
        "authorization_code": "ABC123",
        "approved": True,
        "terminal_id": "TERM001",
        "merchant_category_code": "5411",
        "merchant_name": "Starbucks",
    }


@pytest.fixture
def sample_card_settlement_event() -> Dict[str, Any]:
    """Sample valid CardSettlementEvent payload."""
    event_id = str(uuid4())
    return {
        "event_id": event_id,
        "schema_version": 1,
        "rail": "CARD",
        "event_type": "SETTLEMENT",
        "status": "SETTLED",
        "sender_id": "cardholder_hash_123",
        "receiver_id": "merchant_hash_456",
        "amount_cents": 5000,
        "currency": "USD",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "raw_payload": {
            "authorization_code": "ABC123",
            "settlement_date": "2026-06-26",
        },
        "authorization_code": "ABC123",
        "settlement_amount_cents": 5000,
        "settled_at_utc": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def sample_invalid_event() -> Dict[str, Any]:
    """Sample invalid event (missing required fields)."""
    return {
        "event_id": str(uuid4()),
        "rail": "CARD",
        # Missing: event_type, sender_id, receiver_id, etc.
    }


@pytest.fixture
def mock_postgres_client() -> AsyncMock:
    """Create a mock PostgreSQL client."""
    mock = AsyncMock()
    mock.save_transaction = AsyncMock()
    mock.insert_outbox = AsyncMock()
    mock.fetch_pending_outbox = AsyncMock(return_value=[])
    mock.mark_outbox_synced = AsyncMock()
    mock.mark_outbox_failed = AsyncMock()
    mock.increment_outbox_retry = AsyncMock()
    mock.get_outbox_stats = AsyncMock(
        return_value={
            "pending": {"count": 0, "max_age_seconds": 0, "avg_age_seconds": 0},
            "synced": {"count": 0, "max_age_seconds": 0, "avg_age_seconds": 0},
            "failed": {"count": 0, "max_age_seconds": 0, "avg_age_seconds": 0},
        }
    )
    mock.health_check = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_neo4j_client() -> AsyncMock:
    """Create a mock Neo4j client."""
    mock = AsyncMock()
    mock.upsert_transaction_graph = AsyncMock()
    mock.create_account_node = AsyncMock()
    mock.get_subgraph = AsyncMock(return_value={"nodes": [], "edges": []})
    mock.find_cycles = AsyncMock(return_value=[])
    mock.shortest_path = AsyncMock(return_value=None)
    mock.health_check = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_redis_client() -> AsyncMock:
    """Create a mock Redis client."""
    mock = AsyncMock()
    mock.add_edge_to_timeseries = AsyncMock()
    mock.get_edge_volume_in_window = AsyncMock(
        return_value={
            "total_volume_cents": 0,
            "transaction_count": 0,
            "earliest_timestamp": None,
            "latest_timestamp": None,
        }
    )
    mock.cache_set = AsyncMock()
    mock.cache_get = AsyncMock(return_value=None)
    mock.health_check = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_dlq_handler() -> AsyncMock:
    """Create a mock DLQ handler."""
    mock = AsyncMock()
    mock.route_to_dlq = AsyncMock()
    mock.route_sync_failure = AsyncMock()
    return mock
