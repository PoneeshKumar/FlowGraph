"""
Machine-learning layer for FlowGraph.

Turns the live graph into training tensors. The GNN is the primary risk
classifier; cycle detection and Louvain act as feature providers and weak
label sources rather than as the final verdict.

  features.py — assembles per-account feature vectors + edge_index into a
                PyG-ready graph (FeatureBuilder / FeatureSet)

Requires requirements-ml.txt. Nothing in the Faust consumer or the outbox
worker imports this package, which is why torch is not in requirements.txt.
"""
