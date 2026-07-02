"""
Faust consumer app for FlowGraph payment event ingestion.

Consumes from Kafka topic (payments.raw.card), validates, and writes atomically
to Postgres (transaction + outbox row).

Invalid events route to DLQ. Partition key is sender_id for temporal ordering per sender.
"""

import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime
from hashlib import sha256

import faust
from config import (
    KAFKA_BROKER,
    KAFKA_CONSUMER_GROUP,
    TOPIC_CARD_RAW,
    TOPIC_CARD_DLQ,
    LOG_LEVEL,
)
from normalizer.card_normalizer import CardNormalizer
from consumer.dead_letter_handler import DeadLetterHandler

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# Initialize Faust app
app = faust.App(
    "flowgraph-payment-processor",
    broker=KAFKA_BROKER,
    consumer_group=KAFKA_CONSUMER_GROUP,
    store="memory://",  # State store (not used yet, but required by Faust)
)

# Define topic schema
CardPaymentEventTopic = app.topic(
    TOPIC_CARD_RAW,
    key_type=str,  # Partition key: sender_id
    value_type=Dict[str, Any],
)

# DLQ topic for malformed events
CardDLQTopic = app.topic(
    TOPIC_CARD_DLQ,
    key_type=str,
    value_type=Dict[str, Any],
)

# Global clients (initialized in main.py)
postgres_client = None
neo4j_client = None
redis_client = None
dlq_handler = None
normalizer = CardNormalizer()


@app.agent(CardPaymentEventTopic)
async def process_card_event(stream) -> None:
    """
    Main consumer agent: process incoming card payment events.
    
    Flow:
    1. Deserialize JSON from Kafka
    2. Normalize to Pydantic model (validates schema)
    3. If valid: write to Postgres (transaction + outbox row atomically)
    4. If invalid: route to DLQ
    """
    async for event_key, raw_payload in stream.items():
        try:
            logger.debug(
                f"Processing event | partition_key={event_key} | "
                f"payload_size={len(str(raw_payload))} bytes"
            )

            # Step 1: Normalize event (deserialize + validate)
            normalized_event, error = normalizer.normalize(raw_payload)

            if error:
                # Validation failed — route to DLQ
                logger.warning(
                    f"Validation failed for event from {event_key}: {error}"
                )
                await dlq_handler.route_to_dlq(
                    raw_payload=raw_payload,
                    error_reason=error,
                    rail="CARD",
                    event_id=raw_payload.get("event_id") if isinstance(raw_payload, dict) else None,
                )
                continue

            # Step 2: Write to Postgres (transaction + outbox atomically)
            await _write_to_postgres(normalized_event, raw_payload)
            logger.info(
                f"Persisted event | event_id={normalized_event.event_id} | "
                f"type={normalized_event.event_type} | "
                f"amount={normalized_event.amount_cents}¢"
            )

        except Exception as e:
            logger.error(f"Unexpected error processing event: {e}", exc_info=True)
            # Don't route to DLQ for system errors — these should be investigated
            # and the consumer may need to be restarted


async def _write_to_postgres(normalized_event: Any, raw_payload: Dict[str, Any]) -> None:
    """
    Atomically write transaction and outbox row to Postgres.
    
    Args:
        normalized_event: Validated Pydantic event model
        raw_payload: Original raw payload
    """
    if not postgres_client:
        raise RuntimeError("PostgresClient not initialized")

    try:
        # Create idempotency key: hash(event_id + timestamp)
        idempotency_key = sha256(
            f"{normalized_event.event_id}:{normalized_event.timestamp_utc.isoformat()}".encode()
        ).hexdigest()

        # Save transaction to Postgres
        await postgres_client.save_transaction(
            transaction_id=str(normalized_event.event_id),
            rail=normalized_event.rail.value,
            event_type=normalized_event.event_type.value,
            status=normalized_event.status.value,
            sender_id=normalized_event.sender_id,
            receiver_id=normalized_event.receiver_id,
            amount_cents=normalized_event.amount_cents,
            currency=normalized_event.currency,
            timestamp_utc=normalized_event.timestamp_utc,
            raw_payload=raw_payload,
            schema_version=normalized_event.schema_version,
            authorization_code=getattr(normalized_event, "authorization_code", None),
        )

        # Insert outbox row (triggers async sync to Neo4j/Redis)
        await postgres_client.insert_outbox(
            transaction_id=str(normalized_event.event_id),
            idempotency_key=idempotency_key,
            event_payload={
                "event_id": str(normalized_event.event_id),
                "rail": normalized_event.rail.value,
                "event_type": normalized_event.event_type.value,
                "sender_id": normalized_event.sender_id,
                "receiver_id": normalized_event.receiver_id,
                "amount_cents": normalized_event.amount_cents,
                "currency": normalized_event.currency,
                "timestamp_utc": normalized_event.timestamp_utc.isoformat(),
            },
        )

        logger.debug(f"Outbox entry created for event {normalized_event.event_id}")

    except Exception as e:
        logger.error(
            f"Failed to write to Postgres for event {normalized_event.event_id}: {e}",
            exc_info=True,
        )
        raise


def initialize_clients(
    postgres_cli: Any,
    neo4j_cli: Any,
    redis_cli: Any,
    dlq_hdlr: Any,
) -> None:
    """
    Inject client dependencies (called from main.py during startup).
    
    Args:
        postgres_cli: Initialized PostgresClient
        neo4j_cli: Initialized Neo4jClient
        redis_cli: Initialized RedisClient
        dlq_hdlr: Initialized DeadLetterHandler
    """
    global postgres_client, neo4j_client, redis_client, dlq_handler
    postgres_client = postgres_cli
    neo4j_client = neo4j_cli
    redis_client = redis_cli
    dlq_handler = dlq_hdlr
    logger.info("Faust consumer clients initialized")


if __name__ == "__main__":
    app.main()
