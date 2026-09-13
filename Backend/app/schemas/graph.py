from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

class NodeData(BaseModel):
    id: str
    label: str
    node_type: str = "account"
    risk_score: float = 0.0
    risk_tier: str = "low"  # low, medium, high, critical
    community_id: Optional[int] = None
    pagerank_score: Optional[float] = 0.0
    attributes: Dict[str, Any] = Field(default_factory=dict)

class NodeElement(BaseModel):
    data: NodeData

class EdgeData(BaseModel):
    id: str
    source: str
    target: str
    tx_count: int = 1
    total_amount: float = 0.0
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    weight: float = 1.0

class EdgeElement(BaseModel):
    data: EdgeData

class GraphElements(BaseModel):
    nodes: List[NodeElement]
    edges: List[EdgeElement]

class FlowSummaryResponse(BaseModel):
    source: str
    target: str
    window: str
    total_volume_cents: float
    tx_count: int
    avg_amount_cents: float
    path_count: int

class AIReportResponse(BaseModel):
    account_id: str
    risk_level: str
    confidence: float
    explanation: str
    detected_typology: Optional[str]
    compliance_summary: str

class BusinessSummaryRequest(BaseModel):
    business_id: str = Field(..., min_length=1, max_length=128)
    question: str = Field(..., min_length=1, max_length=1000)
    risk_tier: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    limit: int = Field(default=25, ge=1, le=100)

class BusinessSummaryResponse(BaseModel):
    business_id: str
    question: str
    summary: str
    data: Dict[str, Any]
    generated_by: str