"""
Card event normalizer for FlowGraph.

Deserializes raw Kafka JSON into typed Pydantic models with validation.
Routes malformed events to dead-letter queue for investigation.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple, Union
from datetime import datetime

from pydantic import ValidationError

from models.card_events import CardAuthEvent, CardSettlementEvent, EventStatus
from config import LOG_LEVEL

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)


class CardNormalizer:
    """Normalizes raw card payment events into typed Pydantic models."""

    @staticmethod
    def normalize(
        raw_payload: Union[str, Dict[str, Any]]
    ) -> Tuple[Optional[Union[CardAuthEvent, CardSettlementEvent]], Optional[str]]:
        """
        Deserialize and validate a raw card payment event.
        
        Args:
            raw_payload: JSON string or dict containing raw event data
        
        Returns:
            Tuple of (normalized_event, error_message)
            - If successful: (event_object, None)
            - If failed: (None, error_reason_string)
        """
        # Parse JSON if needed
        if isinstance(raw_payload, str):
            try:
                parsed = json.loads(raw_payload)
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON: {str(e)}"
                logger.warning(f"Failed to parse JSON: {error_msg}")
                return None, error_msg
        else:
            parsed = raw_payload

        # Determine event type and route to appropriate model
        event_type = parsed.get("event_type", "").upper()

        try:
            if event_type == "AUTH":
                event = CardAuthEvent(**parsed)
                logger.debug(f"Normalized AUTH event: {event.event_id}")
                return event, None

            elif event_type == "SETTLEMENT":
                event = CardSettlementEvent(**parsed)
                logger.debug(f"Normalized SETTLEMENT event: {event.event_id}")
                return event, None

            else:
                error_msg = f"Unknown event_type: {event_type}. Expected AUTH or SETTLEMENT."
                logger.warning(error_msg)
                return None, error_msg

        except ValidationError as e:
            # Pydantic validation failed
            error_msg = f"Validation error: {e.errors()}"
            logger.warning(f"Event validation failed: {error_msg}")
            return None, error_msg

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Failed to normalize event: {error_msg}")
            return None, error_msg

    @staticmethod
    def validate_auth_settlement_match(
        auth_event: "CardAuthEvent", settlement_event: "CardSettlementEvent"
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that an auth and settlement event match correctly.
        
        Args:
            auth_event: Normalized AUTH event
            settlement_event: Normalized SETTLEMENT event
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check authorization code match
        if auth_event.authorization_code != settlement_event.authorization_code:
            return False, "Authorization codes do not match"

        # Check sender/receiver match
        if (auth_event.sender_id != settlement_event.sender_id or
            auth_event.receiver_id != settlement_event.receiver_id):
            return False, "Sender/receiver mismatch between auth and settlement"

        # Check currency match
        if auth_event.currency != settlement_event.currency:
            return False, "Currency mismatch between auth and settlement"

        # Settlement amount should be >= auth amount (can have tip)
        if settlement_event.settlement_amount_cents < auth_event.amount_cents:
            return False, "Settlement amount less than auth amount (impossible)"

        return True, None
