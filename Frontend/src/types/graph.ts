export type RiskTier = 'low' | 'medium' | 'high' | 'critical';

export interface NodeData {
  id: string;
  label: string;
  node_type: string;
  risk_score: number;
  risk_tier: RiskTier;
  community_id?: number;
  pagerank_score?: number;
  attributes?: Record<string, any>;
}

export interface EdgeData {
  id: string;
  source: string;
  target: string;
  tx_count: number;
  total_amount: number;
  first_ts?: number;
  last_ts?: number;
  weight: number;
}

export interface CytoscapeNode {
  data: NodeData;
}

export interface CytoscapeEdge {
  data: EdgeData;
}

export interface GraphElements {
  nodes: CytoscapeNode[];
  edges: CytoscapeEdge[];
}

export interface AIReport {
  account_id: string;
  risk_level: RiskTier;
  confidence: number;
  explanation: string;
  detected_typology?: string;
  compliance_summary: string;
}