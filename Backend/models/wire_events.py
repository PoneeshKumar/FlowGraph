# Backend/models/wire.py
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional, List
from datetime import datetime, date

from models.card_events import (
    BasePaymentEvent,
    EventStatus,
    EventType,
    Rail,
)


# transfer scope: domestic within jurisdiction or international across borders
class TransferScope(str, Enum):
    DOMESTIC = "DOMESTIC"
    INTERNATIONAL = "INTERNATIONAL"

# wire rails available across different jurisdictions
class DomesticRail(str, Enum):
    FEDWIRE = "FEDWIRE"
    RTP = "RTP"
    CHAPS = "CHAPS"
    FASTER_PAYMENTS = "FASTER_PAYMENTS"
    BACS = "BACS"
    TARGET2 = "TARGET2"
    SEPA_CT = "SEPA_CT"
    SEPA_INST = "SEPA_INST"
    LYNX = "LYNX"
    HVPS = "HVPS"

# wire network message format
class WireNetwork(str, Enum):
    SWIFT = "SWIFT"
    FEDWIRE_MSG = "FEDWIRE_MSG"
    SEPA_XML = "SEPA_XML"
    FASTER_PAYMENTS_MSG = "FASTER_PAYMENTS_MSG"
    CHIPS = "CHIPS"

# how the wire settles: real-time gross, deferred net, or instant
class SettlementFinality(str, Enum):
    REAL_TIME_GROSS = "RTGS"
    DEFERRED_NET = "DNS"
    INSTANT = "INST"


# jurisdiction risk tiers for AML/sanctions screening
class JurisdictionRiskTier(str, Enum):
    TIER_1_LOW = "LOW"
    TIER_2_MEDIUM = "MEDIUM"
    TIER_3_HIGH = "HIGH"
    TIER_4_CRITICAL = "CRITICAL"

# geographic and jurisdictional risk assessment for wire transfer
class GeoRiskProfile(BaseModel):
    sender_country: str = Field(..., min_length=2, max_length=2)
    receiver_country: str = Field(..., min_length=2, max_length=2)
    correspondent_countries: List[str] = Field(default_factory=list)
    sender_risk_tier: JurisdictionRiskTier
    receiver_risk_tier: JurisdictionRiskTier
    max_chain_risk_tier: JurisdictionRiskTier
    crosses_jurisdiction: bool
    ofac_hit: bool = False
    fatf_grey_list_hit: bool = False
    offshore_center: bool = False
    herstatt_risk: bool = False


# regulatory compliance flags for wire transfer
class RegulatoryProfile(BaseModel):
    ctr_triggered: bool = False
    ctr_threshold_currency: str = Field(default="USD", min_length=3, max_length=3)
    ctr_threshold_amount: int = Field(default=1_000_000, gt=0)
    structuring_flag: bool = False
    is_round_number: bool = False
    travel_rule_applies: bool = False
    travel_rule_complete: bool = True
    beneficiary_info_score: float = Field(default=1.0, ge=0.0, le=1.0)
    is_off_hours: bool = False
    sanctions_screened: bool = False
    sanctions_hit: bool = False


# correspondent bank in a wire transfer chain
class CorrespondentHop(BaseModel):
    bank_id: str = Field(..., min_length=1, max_length=64)
    bank_name: str = Field(..., min_length=1, max_length=128)
    country: str = Field(..., min_length=2, max_length=2)
    role: str = Field(..., min_length=1, max_length=32)


# raw fedwire event from inbound message
class RawFedwireEvent(BaseModel):
    imad: str = Field(..., min_length=1, max_length=22)
    omad: Optional[str] = Field(default=None, max_length=22)
    sender_aba: str = Field(..., min_length=9, max_length=9, pattern=r"^\d{9}$")
    sender_account: str = Field(..., min_length=1, max_length=34)
    sender_name: str = Field(..., min_length=1, max_length=35)
    sender_state: Optional[str] = Field(default=None, min_length=2, max_length=2)
    receiver_aba: str = Field(..., min_length=9, max_length=9, pattern=r"^\d{9}$")
    receiver_account: str = Field(..., min_length=1, max_length=34)
    receiver_name: str = Field(..., min_length=1, max_length=35)
    receiver_state: Optional[str] = Field(default=None, min_length=2, max_length=2)
    amount: int = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    value_date: date
    execution_timestamp: datetime
    business_function_code: str = Field(..., min_length=3, max_length=3)
    type_subtype_code: str = Field(default="1000", min_length=4, max_length=4)
    originator_to_beneficiary: Optional[str] = Field(default=None, max_length=140)
    domestic_rail: DomesticRail = DomesticRail.FEDWIRE
    wire_network: WireNetwork = WireNetwork.FEDWIRE_MSG


# raw SWIFT MT103 event for international wires
class RawSWIFTEvent(BaseModel):
    uetr: str = Field(..., min_length=36, max_length=36)
    message_type: str = Field(default="MT103", min_length=5, max_length=5)
    sender_bic: str = Field(..., min_length=8, max_length=11)
    sender_country: str = Field(..., min_length=2, max_length=2)
    receiver_bic: str = Field(..., min_length=8, max_length=11)
    receiver_country: str = Field(..., min_length=2, max_length=2)
    ordering_customer_account: str = Field(..., min_length=1, max_length=34)
    ordering_customer_name: str = Field(..., min_length=1, max_length=35)
    ordering_customer_address: Optional[str] = Field(default=None, max_length=140)
    beneficiary_account: str = Field(..., min_length=1, max_length=34)
    beneficiary_name: str = Field(..., min_length=1, max_length=35)
    beneficiary_address: Optional[str] = Field(default=None, max_length=140)
    instructed_amount: int = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    value_date: date
    execution_timestamp: datetime
    correspondent_banks: List[CorrespondentHop] = Field(default_factory=list)
    purpose_code: Optional[str] = Field(default=None, max_length=4)
    charge_type: str = Field(default="SHA", min_length=3, max_length=3)
    wire_network: WireNetwork = WireNetwork.SWIFT
    gpi_tracking_id: Optional[str] = Field(default=None, min_length=36, max_length=36)


# normalized wire event after enrichment from raw payload
# inherits the shared shape from BasePaymentEvent: event_id, rail, event_type,
# status, sender_id, receiver_id, amount_cents, currency, timestamp_utc, raw_payload
class NormalizedWireEvent(BasePaymentEvent):
    rail: Rail = Field(default=Rail.WIRE)
    event_type: EventType = Field(default=EventType.SETTLEMENT)
    status: EventStatus = Field(default=EventStatus.SETTLED)

    source_message_id: str = Field(..., min_length=1, max_length=36)
    sender_bank_id: str = Field(..., min_length=1, max_length=64)
    receiver_bank_id: str = Field(..., min_length=1, max_length=64)
    correspondent_chain: List[str] = Field(default_factory=list)
    value_date: date
    transfer_scope: TransferScope
    domestic_rail: Optional[DomesticRail] = None
    wire_network: WireNetwork
    settlement_finality: SettlementFinality
    is_bank_to_bank: bool = False
    geo_risk: GeoRiskProfile
    regulatory: RegulatoryProfile