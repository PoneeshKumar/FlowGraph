# Backend/models/wire.py
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional, List
from datetime import datetime, date
import uuid

from models.card_events import Rail


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
    sender_country: str
    receiver_country: str
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
    ctr_threshold_currency: str = "USD"
    ctr_threshold_amount: int = 1_000_000
    structuring_flag: bool = False
    is_round_number: bool = False
    travel_rule_applies: bool = False
    travel_rule_complete: bool = True
    beneficiary_info_score: float = 1.0
    is_off_hours: bool = False
    sanctions_screened: bool = False
    sanctions_hit: bool = False


# correspondent bank in a wire transfer chain
class CorrespondentHop(BaseModel):
    bank_id: str
    bank_name: str
    country: str
    role: str


# raw fedwire event from inbound message
class RawFedwireEvent(BaseModel):
    imad: str
    omad: Optional[str] = None
    sender_aba: str
    sender_account: str
    sender_name: str
    sender_state: Optional[str] = None
    receiver_aba: str
    receiver_account: str
    receiver_name: str
    receiver_state: Optional[str] = None
    amount: int
    currency: str = "USD"
    value_date: date
    execution_timestamp: datetime
    business_function_code: str
    type_subtype_code: str = "1000"
    originator_to_beneficiary: Optional[str] = None
    domestic_rail: DomesticRail = DomesticRail.FEDWIRE
    wire_network: WireNetwork = WireNetwork.FEDWIRE_MSG


# raw SWIFT MT103 event for international wires
class RawSWIFTEvent(BaseModel):
    uetr: str
    message_type: str = "MT103"
    sender_bic: str
    sender_country: str
    receiver_bic: str
    receiver_country: str
    ordering_customer_account: str
    ordering_customer_name: str
    ordering_customer_address: Optional[str] = None
    beneficiary_account: str
    beneficiary_name: str
    beneficiary_address: Optional[str] = None
    instructed_amount: int
    currency: str
    value_date: date
    execution_timestamp: datetime
    correspondent_banks: List[CorrespondentHop] = Field(default_factory=list)
    purpose_code: Optional[str] = None
    charge_type: str = "SHA"
    wire_network: WireNetwork = WireNetwork.SWIFT
    gpi_tracking_id: Optional[str] = None


# normalized wire event after enrichment from raw payload
class NormalizedWireEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_message_id: str
    sender_id: str
    receiver_id: str
    sender_bank_id: str
    receiver_bank_id: str
    correspondent_chain: List[str] = Field(default_factory=list)
    amount: int
    currency: str
    amount_usd: Optional[int] = None
    timestamp: datetime
    value_date: date
    rail: Rail = Rail.WIRE
    transfer_scope: TransferScope
    domestic_rail: Optional[DomesticRail] = None
    wire_network: WireNetwork
    settlement_finality: SettlementFinality
    is_bank_to_bank: bool = False
    geo_risk: GeoRiskProfile
    regulatory: RegulatoryProfile
    raw_payload: dict