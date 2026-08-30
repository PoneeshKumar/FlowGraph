"""
Dataset loaders for GNN training.

  ibm_aml.py   — streams HI-*/LI-* transaction CSVs into Neo4j + Redis so the
                 normal FeatureBuilder pipeline can read them. Covers all 8
                 laundering typologies, not just CYCLE.
  elliptic.py  — loads the Elliptic Bitcoin dataset straight into a FeatureSet,
                 bypassing the graph stores (its features are pre-computed).

Both ultimately produce a FeatureSet, so the same model and training code runs
against either.
"""
