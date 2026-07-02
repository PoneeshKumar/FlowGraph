"""
Tests for card payment event models.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import ValidationError

from models.card_events import (
    BasePaymentEvent,
    CardAuthEvent,
    CardSettlementEvent,
    Rail,
    EventType,
    EventStatus,
)


class TestCardAuthEvent:
    """Tests for CardAuthEvent model."""

    def test_valid_auth_event(self, sample_card_auth_event):
        """Test creating a valid CardAuthEvent."""
        event = CardAuthEvent(**sample_card_auth_event)
        
        assert event.rail == Rail.CARD
        assert event.event_type == EventType.AUTH
        assert event.status == EventStatus.PENDING
        assert event.sender_id == "cardholder_hash_123"
        assert event.receiver_id == "merchant_hash_456"
        assert event.amount_cents == 5000
        assert event.currency == "USD"

    def test_auth_event_currency_uppercase(self, sample_card_auth_event):
        """Test that currency is auto-uppercased."""
        sample_card_auth_event["currency"] = "usd"
        event = CardAuthEvent(**sample_card_auth_event)
        
        assert event.currency == "USD"

    def test_auth_event_missing_sender_id(self, sample_card_auth_event):
        """Test validation fails when sender_id is missing."""
        del sample_card_auth_event["sender_id"]
        
        with pytest.raises(ValidationError):
            CardAuthEvent(**sample_card_auth_event)

    def test_auth_event_invalid_amount(self, sample_card_auth_event):
        """Test validation fails when amount_cents is negative or zero."""
        sample_card_auth_event["amount_cents"] = 0
        
        with pytest.raises(ValidationError):
            CardAuthEvent(**sample_card_auth_event)

    def test_auth_event_naive_timestamp_rejected(self, sample_card_auth_event):
        """Test validation fails for naive (non-UTC) timestamps."""
        # Create a naive datetime (no timezone info)
        sample_card_auth_event["timestamp_utc"] = datetime.now()
        
        with pytest.raises(ValidationError):
            CardAuthEvent(**sample_card_auth_event)

    def test_auth_event_utc_timestamp_accepted(self, sample_card_auth_event):
        """Test that UTC-aware timestamps are accepted."""
        utc_time = datetime.now(timezone.utc)
        sample_card_auth_event["timestamp_utc"] = utc_time
        
        event = CardAuthEvent(**sample_card_auth_event)
        assert event.timestamp_utc.tzinfo is not None

    def test_auth_event_raw_payload_preserved(self, sample_card_auth_event):
        """Test that raw_payload is preserved."""
        raw = {"original": "data", "nested": {"key": "value"}}
        sample_card_auth_event["raw_payload"] = raw
        
        event = CardAuthEvent(**sample_card_auth_event)
        assert event.raw_payload == raw


class TestCardSettlementEvent:
    """Tests for CardSettlementEvent model."""

    def test_valid_settlement_event(self, sample_card_settlement_event):
        """Test creating a valid CardSettlementEvent."""
        event = CardSettlementEvent(**sample_card_settlement_event)
        
        assert event.rail == Rail.CARD
        assert event.event_type == EventType.SETTLEMENT
        assert event.status == EventStatus.SETTLED
        assert event.settlement_amount_cents == 5000

    def test_settlement_with_tip(self, sample_card_settlement_event):
        """Test settlement with tip (amount > auth)."""
        sample_card_settlement_event["settlement_amount_cents"] = 5500  # $5000 + $5 tip
        
        event = CardSettlementEvent(**sample_card_settlement_event)
        assert event.settlement_amount_cents == 5500

    def test_settlement_missing_settlement_amount(self, sample_card_settlement_event):
        """Test validation fails when settlement_amount_cents is missing."""
        del sample_card_settlement_event["settlement_amount_cents"]
        
        with pytest.raises(ValidationError):
            CardSettlementEvent(**sample_card_settlement_event)


class TestEventEnums:
    """Tests for event enums."""

    def test_rail_enum_values(self):
        """Test Rail enum has all expected values."""
        assert Rail.CARD.value == "CARD"
        assert Rail.WIRE.value == "WIRE"
        assert Rail.ACH.value == "ACH"
        assert Rail.CRYPTO.value == "CRYPTO"

    def test_event_type_enum_values(self):
        """Test EventType enum has all expected values."""
        assert EventType.AUTH.value == "AUTH"
        assert EventType.SETTLEMENT.value == "SETTLEMENT"

    def test_event_status_enum_values(self):
        """Test EventStatus enum has all expected values."""
        assert EventStatus.PENDING.value == "PENDING"
        assert EventStatus.SETTLED.value == "SETTLED"
        assert EventStatus.DECLINED.value == "DECLINED"
        assert EventStatus.ORPHANED.value == "ORPHANED"
