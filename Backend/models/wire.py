# models/wire_extended.py
from pydantic import BaseModel, Field, field_validator
from enum import Enum
from typing import Optional, List
from datetime import datetime, date
import uuid

from models.card_events import Rail


# ─── Rail taxonomy ────────────────────────────────────────────────────────────

class TransferScope(str, Enum):
    DOMESTIC     = "DOMESTIC"      # within one jurisdiction's clearing system
    INTERNATIONAL = "INTERNATIONAL" # crosses jurisdictional boundary

class DomesticRail(str, Enum):
    # United States
    FEDWIRE          = "FEDWIRE"           # large-value, real-time, irrevocable
    RTP              = "RTP"               # Real-Time Payments (TCH), up to $1M
    # United Kingdom
    CHAPS            = "CHAPS"             # large-value, same-day final
    FASTER_PAYMENTS  = "FASTER_PAYMENTS"   # retail, up to £250k, near-instant
    BACS             = "BACS"              # batch, 3-day settlement (like ACH)
    # European Union
    TARGET2          = "TARGET2"           # ECB large-value, EUR only
    SEPA_CT          = "SEPA_CT"           # SEPA credit transfer, pan-EU
    SEPA_INST        = "SEPA_INST"         # SEPA Instant, 10-second settlement
    # Other
    LYNX             = "LYNX"              # Canada large-value
    HVPS             = "HVPS"              # China high-value payment system

class WireNetwork(str, Enum):
    SWIFT            = "SWIFT"             # international message standard
    FEDWIRE_MSG      = "FEDWIRE_MSG"       # Fedwire-native format
    SEPA_XML         = "SEPA_XML"          # ISO 20022 pain.001
    FASTER_PAYMENTS_MSG = "FASTER_PAYMENTS_MSG"
    CHIPS            = "CHIPS"

class SettlementFinality(str, Enum):
    REAL_TIME_GROSS  = "RTGS"   # irrevocable on receipt (Fedwire, CHAPS)
    DEFERRED_NET     = "DNS"    # nets at end of day (BACS, SEPA-CT)
    INSTANT          = "INST"   # near-instant, irrevocable (RTP, SEPA-INST)


# ─── Geographic risk profile ──────────────────────────────────────────────────

class JurisdictionRiskTier(str, Enum):
    TIER_1_LOW       = "LOW"        # US, UK, EU, CA, AU, JP, SG
    TIER_2_MEDIUM    = "MEDIUM"     # BR, IN, MX, TH, AE
    TIER_3_HIGH      = "HIGH"       # FATF grey list
    TIER_4_CRITICAL  = "CRITICAL"   # FATF blacklist / OFAC sanctioned

class GeoRiskProfile(BaseModel):
    sender_country:        str                    # ISO-3166 alpha-2
    receiver_country:      str
    correspondent_countries: List[str] = []       # countries in the chain
    sender_risk_tier:      JurisdictionRiskTier
    receiver_risk_tier:    JurisdictionRiskTier
    max_chain_risk_tier:   JurisdictionRiskTier   # worst tier in correspondent chain
    crosses_jurisdiction:  bool                   # True for international
    ofac_hit:              bool = False           # placeholder — real impl needs licensed data
    fatf_grey_list_hit:    bool = False           # either endpoint on FATF grey list
    offshore_center:       bool = False           # BVI, Cayman, Panama, etc.
    herstatt_risk:         bool = False           # cross-timezone cross-currency settlement


# ─── Regulatory compliance profile ───────────────────────────────────────────

class RegulatoryProfile(BaseModel):
    # FinCEN / CTR thresholds — jurisdiction-specific
    ctr_triggered:         bool = False    # amount >= reporting threshold in local currency
    ctr_threshold_currency: str = "USD"
    ctr_threshold_amount:  int = 1_000_000 # cents — $10k

    # Structuring detection — amounts suspiciously close to, but under, threshold
    structuring_flag:      bool = False    # amount in [threshold*0.80, threshold*0.99]

    # Round-number flag — $50,000.00 exactly is a fraud signal
    is_round_number:       bool = False

    # SWIFT Travel Rule — for transfers > $3,000, originator + beneficiary info required
    travel_rule_applies:   bool = False
    travel_rule_complete:  bool = True    # False = missing mandatory fields

    # Data quality — missing/truncated beneficiary info is itself a SAR trigger
    beneficiary_info_score: float = 1.0  # 1.0 = complete, 0.0 = entirely missing

    # Off-hours flag — wires initiated outside business hours are elevated risk
    is_off_hours:          bool = False

    # Sanctions placeholder
    sanctions_screened:    bool = False
    sanctions_hit:         bool = False


# ─── Correspondent hop (unchanged from v1, reproduced for completeness) ────────

class CorrespondentHop(BaseModel):
    bank_id:   str
    bank_name: str
    country:   str
    role:      str   # "INTERMEDIARY" | "CORRESPONDENT" | "COVER"


# ─── Raw inbound — extended Fedwire ──────────────────────────────────────────

class RawFedwireEvent(BaseModel):
    imad:                      str
    omad:                      Optional[str] = None
    sender_aba:                str
    sender_account:            str
    sender_name:               str
    sender_state:              Optional[str] = None  # NEW: US state, for domestic geo
    receiver_aba:              str
    receiver_account:          str
    receiver_name:             str
    receiver_state:            Optional[str] = None
    amount:                    int
    currency:                  str = "USD"
    value_date:                date
    execution_timestamp:       datetime
    business_function_code:    str                   # CTR | BTR
    type_subtype_code:         str = "1000"
    originator_to_beneficiary: Optional[str] = None
    domestic_rail:             DomesticRail = DomesticRail.FEDWIRE
    wire_network:              WireNetwork = WireNetwork.FEDWIRE_MSG


# ─── Raw inbound — SWIFT MT103 (international) ────────────────────────────────

class RawSWIFTEvent(BaseModel):
    uetr:                       str
    message_type:               str = "MT103"
    sender_bic:                 str
    sender_country:             str    # NEW: derived from BIC position 5-6
    receiver_bic:               str
    receiver_country:           str    # NEW
    ordering_customer_account:  str
    ordering_customer_name:     str
    ordering_customer_address:  Optional[str] = None  # Travel Rule field
    beneficiary_account:        str
    beneficiary_name:           str
    beneficiary_address:        Optional[str] = None  # Travel Rule field
    instructed_amount:          int
    currency:                   str
    value_date:                 date
    execution_timestamp:        datetime
    correspondent_banks:        List[CorrespondentHop] = []
    purpose_code:               Optional[str] = None
    charge_type:                str = "SHA"
    wire_network:               WireNetwork = WireNetwork.SWIFT
    gpi_tracking_id:            Optional[str] = None  # SWIFT gpi UETR alias


# ─── Canonical normalized event — fully enriched ──────────────────────────────

class NormalizedWireEvent(BaseModel):
    """
    Single shape for all wire variants after normalization + enrichment.
    The geo_risk and regulatory fields are computed by the enrichment layer —
    the graph writer and fraud algorithms read from these, not the raw payload.
    """
    event_id:             str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_message_id:    str                   # IMAD or UETR
    sender_id:            str                   # WIRE:{bank_id}:{account}
    receiver_id:          str
    sender_bank_id:       str
    receiver_bank_id:     str
    correspondent_chain:  List[str] = []
    amount:               int
    currency:             str
    amount_usd:           Optional[int] = None
    timestamp:            datetime
    value_date:           date
    rail:                 Rail = Rail.WIRE
    transfer_scope:       TransferScope          # DOMESTIC or INTERNATIONAL
    domestic_rail:        Optional[DomesticRail] = None  # set if DOMESTIC
    wire_network:         WireNetwork
    settlement_finality:  SettlementFinality
    is_bank_to_bank:      bool = False
    geo_risk:             GeoRiskProfile
    regulatory:           RegulatoryProfile
    raw_payload:          dict