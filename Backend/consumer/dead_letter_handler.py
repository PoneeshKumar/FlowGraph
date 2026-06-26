"""
Dead-letter handler for FlowGraph.

Routes malformed or failed events to dead-letter queue topics for investigation.
"""

import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from config import (
    TOPIC_CARD_DLQ,
    TOPIC_WIRE_DLQ,
    TOPIC_ACH_DLQ,
    TOPIC_CRYPTO_DLQ,
    LOG_LEVEL,
)

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)


class DeadLetterHandler:
    """Routes malformed events to dead-letter queues."""

    # Mapping of rails to their DLQ topics
    DLQ_TOPICS = {
        "CARD": TOPIC_CARD_DLQ,
        "WIRE": TOPIC_WIRE_DLQ,
        "ACH": TOPIC_ACH_DLQ,
        "CRYPTO": TOPIC_CRYPTO_DLQ,
    }

    def __init__(self, kafka_producer: Any = None):
        """
        Initialize handler with optional Kafka producer.
        
        Args:
            kafka_producer: Async Kafka producer (from Faust or AIOKafkaProducer)
                           If None, only logs; doesn't actually send to Kafka
        """
        self.producer = kafka_producer

    async def route_to_dlq(
        self,
        raw_payload: Dict[str, Any],
        error_reason: str,
        rail: str = "CARD",
        event_id: Optional[str] = None,
    ) -> None:
        """
        Send malformed event to dead-letter queue.
        
        Creates an enriched DLQ message with original payload + error metadata.
        
        Args:
            raw_payload: Original raw event that failed validation
            error_reason: Human-readable error message
            rail: Payment rail (CARD, WIRE, ACH, CRYPTO)
            event_id: Event ID (if available)
        """
        dlq_message = {
            "dlq_timestamp": datetime.utcnow().isoformat(),
            "event_id": event_id,
            "rail": rail,
            "error_reason": error_reason,
            "original_payload": raw_payload,
        }

        dlq_topic = self.DLQ_TOPICS.get(rail, TOPIC_CARD_DLQ)

        try:
            if self.producer:
                # Send to Kafka DLQ topic
                await self.producer.send_and_wait(
                    dlq_topic,
                    json.dumps(dlq_message).encode("utf-8"),
                )
                logger.info(
                    f"Routed to DLQ: {dlq_topic} | "
                    f"event_id={event_id} | reason={error_reason}"
                )
            else:
                # Just log if no producer configured
                logger.warning(
                    f"DLQ (no producer): {dlq_topic} | "
                    f"event_id={event_id} | reason={error_reason}"
                )
        except Exception as e:
            logger.error(f"Failed to route to DLQ: {e}")
            # Don't raise — log and continue to avoid blocking the consumer

    async def route_sync_failure(
        self,
        transaction_id: str,
        error_reason: str,
        rail: str = "CARD",
        retry_count: int = 0,
    ) -> None:
        """
        Log a transaction that failed to sync to Neo4j/Redis.
        
        This is different from validation failures — the event was valid
        but the downstream graph/cache sync failed after max retries.
        
        Args:
            transaction_id: UUID of transaction that failed to sync
            error_reason: Error from Neo4j/Redis sync attempt
            rail: Payment rail
            retry_count: Number of retry attempts
        """
        sync_failure_message = {
            "dlq_timestamp": datetime.utcnow().isoformat(),
            "transaction_id": transaction_id,
            "rail": rail,
            "sync_error": error_reason,
            "retry_count": retry_count,
            "context": "Neo4j/Redis sync failure after max retries",
        }

        dlq_topic = f"{self.DLQ_TOPICS.get(rail, TOPIC_CARD_DLQ)}.sync-failures"

        try:
            if self.producer:
                await self.producer.send_and_wait(
                    dlq_topic,
                    json.dumps(sync_failure_message).encode("utf-8"),
                )
                logger.error(
                    f"Sync failure routed to DLQ: {dlq_topic} | "
                    f"tx_id={transaction_id} | retries={retry_count}"
                )
            else:
                logger.error(
                    f"Sync failure (no producer): {dlq_topic} | "
                    f"tx_id={transaction_id} | retries={retry_count}"
                )
        except Exception as e:
            logger.error(f"Failed to log sync failure: {e}")

    @staticmethod
    def log_dlq_stats() -> None:
        """Log current DLQ configuration for debugging."""
        logger.info("DLQ Topics Configuration:")
        for rail, topic in DeadLetterHandler.DLQ_TOPICS.items():
            logger.info(f"  {rail} → {topic}")
