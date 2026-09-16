"""Pydantic response/request models for the showcase read API (spec §6, §7)."""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

RISK_LEVELS = ("low", "medium", "high", "critical")
FLAG_STATUSES = ("open", "reviewed", "dismissed", "escalated")
TYPOLOGIES = ("CYCLE", "FAN-OUT", "FAN-IN", "GATHER-SCATTER", "SCATTER-GATHER",
              "BIPARTITE", "STACK", "RANDOM")


class AlertOut(BaseModel):
    id: int
    flag_type: str
    risk_level: str
    risk_score: float
    explanation: str
    account_ids: List[str]
    primary_account: Optional[str]
    details: Dict[str, Any] = Field(default_factory=dict)
    status: str
    first_detected_at: Optional[str]
    last_detected_at: Optional[str]
    detection_count: int


class AlertPage(BaseModel):
    total: int
    items: List[AlertOut]


class AlertStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(open|reviewed|dismissed|escalated)$")


class TransactionOut(BaseModel):
    txn_id: str
    sender_id: str
    receiver_id: str
    amount_cents: int
    currency: str
    rail: str
    event_type: Optional[str]
    ts: int
    sender_tier: str
    receiver_tier: str
    risk_tier: str
    flagged: bool


class TransactionPage(BaseModel):
    items: List[TransactionOut]


class UploadAccepted(BaseModel):
    run_id: str


class LLMStatus(BaseModel):
    provider: str            # anthropic | ollama | none
    model: Optional[str]
    reachable: bool


class ModelJSON(BaseModel):
    """What a language model must return — the schema handed to the provider."""
    risk_level: str = Field(..., pattern="^(low|medium|high|critical)$")
    confidence: float = Field(..., ge=0.0, le=1.0)
    explanation: str = Field(..., min_length=1, max_length=1200)
    detected_typology: Optional[str] = None
    compliance_summary: str = Field(..., min_length=1, max_length=400)


class ExplanationOut(ModelJSON):
    """The API's explanation record: the model's fields plus provenance."""
    account_id: str
    provider: str
    model: Optional[str]
    generated_at: int
