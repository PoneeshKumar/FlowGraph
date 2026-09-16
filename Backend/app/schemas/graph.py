from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field

class NodeData(BaseModel):
    id: str
    label: str
    node_type: str = "account"
    risk_score: float = 0.0          # aggregator verdict if one was written, else the GNN score
    risk_tier: str = "low"           # low, medium, high, critical
    gnn_risk_score: Optional[float] = None
    gnn_risk_tier: Optional[str] = None
    in_cycle: bool = False
    marked: bool = False             # our pipeline's mark (cycle OR GNN ≥ tuned cutoff)
    community_id: Optional[str] = None
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
