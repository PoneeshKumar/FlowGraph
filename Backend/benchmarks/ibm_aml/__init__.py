"""
IBM AML dataset validation harness for the FlowGraph cycle detector.

Pipeline:
  patterns.py  → parse HI-Small_Patterns.txt → CycleGroup list
  ingestor.py  → load CSV, write TRANSFER edges via real upsert_transaction_graph
  runner.py    → orchestrate ingest → detect → compare → BenchmarkResult
  blindspots.py → bucket missed groups by miss cause → BlindspotReport
"""
