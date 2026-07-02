"""
FlowGraph fraud detection engine.

Detectors:
  fraud.cycle_detector  — circular money flows (A→B→C→A)
  fraud.structuring_detector  (planned) — CTR evasion / smurfing

All detectors write into the shared `risk_flags` table via postgres.upsert_risk_flag.
Every flag carries a human-readable explanation (regulatory requirement).
"""
