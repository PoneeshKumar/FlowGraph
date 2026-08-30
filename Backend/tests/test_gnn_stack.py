"""
Smoke tests for the GNN training stack (requirements-ml.txt).

These do not train anything real — they prove the installed stack actually works
end to end before the feature-assembly layer is built on top of it:

  1. the pinned versions are what we think they are
  2. a tensor device is usable (CPU, plus MPS on Apple Silicon)
  3. FLOWS_TO-shaped edge rows convert into a PyG Data object correctly
  4. a 2-layer SAGEConv runs a forward pass and returns per-node logits
  5. the same weights run on a graph containing unseen accounts (inductive)
  6. SMOTE rebalances an imbalanced node-feature matrix

Run with:  python3 -m pytest tests/test_gnn_stack.py
"""

from typing import Dict, List, Tuple

import pytest


# The pins in requirements-ml.txt. Every one is the last release shipping a
# Python 3.9 wheel, so a surprise version here means someone bumped the runtime.
EXPECTED_VERSIONS = {
    "torch": "2.8.0",
    "torch_geometric": "2.6.1",
    "sklearn": "1.6.1",
    "imblearn": "0.12.4",
    "scipy": "1.13.1",
}

# Stand-in for the real node feature vector described in CLAUDE.md. The live
# version will be assembled from Neo4j node properties + pagerank_score +
# community_id + the Redis 1h/24h/7d windowed volumes.
FEATURE_NAMES = [
    "kyc_tier",
    "risk_score",
    "account_age",
    "cumulative_volume",
    "pagerank_score",
    "community_size",
    "volume_1h",
    "volume_24h",
]
NUM_FEATURES = len(FEATURE_NAMES)

# low / medium / high / critical, matching the risk_level enum.
NUM_CLASSES = 4


def _flows_to_rows() -> List[Tuple[str, str, float]]:
    """Edge rows shaped like a FLOWS_TO query result from Neo4j.

    Mirrors what compute_local_pagerank reads back:
    (source_id, target_id, weight) where weight is rel.total_amount.

    The shape encodes a 3-account cycle (a -> b -> c -> a) plus a mule
    fanning out, so it looks like something the cycle detector would flag.
    """
    return [
        ("acct_a", "acct_b", 5_000.0),
        ("acct_b", "acct_c", 4_800.0),
        ("acct_c", "acct_a", 4_650.0),
        ("acct_a", "acct_mule", 12_000.0),
        ("acct_mule", "acct_c", 11_400.0),
    ]


def _build_data(rows: List[Tuple[str, str, float]]):
    """Convert FLOWS_TO rows into a PyG Data object.

    This is a deliberately minimal version of the real feature-assembly step.
    The important part is edge_index: PyG wants a [2, num_edges] tensor of
    integer node offsets, NOT account id strings, so every account has to be
    mapped to a contiguous index first.
    """
    import torch
    from torch_geometric.data import Data

    node_ids: List[str] = []
    index_of: Dict[str, int] = {}
    for source, target, _ in rows:
        for account in (source, target):
            if account not in index_of:
                index_of[account] = len(node_ids)
                node_ids.append(account)

    # edge_index is [2, num_edges]: row 0 is sources, row 1 is targets.
    edge_index = torch.tensor(
        [[index_of[s] for s, _, _ in rows], [index_of[t] for _, t, _ in rows]],
        dtype=torch.long,
    )
    edge_weight = torch.tensor([w for _, _, w in rows], dtype=torch.float)

    # Deterministic stand-in features, one row per account.
    torch.manual_seed(0)
    x = torch.rand((len(node_ids), NUM_FEATURES), dtype=torch.float)

    data = Data(x=x, edge_index=edge_index, edge_weight=edge_weight)
    data.node_ids = node_ids
    return data


def _build_model():
    """A 2-layer GraphSAGE node classifier.

    Two layers means each account's representation is informed by its
    neighbours and its neighbours' neighbours — 2 hops. That is why depth
    matters for catching layering patterns.
    """
    import torch
    from torch_geometric.nn import SAGEConv

    class GraphSAGERiskClassifier(torch.nn.Module):
        def __init__(self, in_channels: int, hidden: int, out_channels: int):
            super().__init__()
            self.conv1 = SAGEConv(in_channels, hidden)
            self.conv2 = SAGEConv(hidden, out_channels)

        def forward(self, x, edge_index):
            x = self.conv1(x, edge_index).relu()
            # Returns raw logits, not probabilities — Focal Loss will apply
            # its own softmax/sigmoid internally.
            return self.conv2(x, edge_index)

    torch.manual_seed(0)
    return GraphSAGERiskClassifier(NUM_FEATURES, hidden=16, out_channels=NUM_CLASSES)


@pytest.mark.unit
def test_ml_stack_imports_and_versions():
    """Every pinned package imports and reports the expected version."""
    import imblearn
    import scipy
    import sklearn
    import torch
    import torch_geometric

    actual = {
        "torch": torch.__version__,
        "torch_geometric": torch_geometric.__version__,
        "sklearn": sklearn.__version__,
        "imblearn": imblearn.__version__,
        "scipy": scipy.__version__,
    }
    assert actual == EXPECTED_VERSIONS


@pytest.mark.unit
def test_tensor_device_is_usable():
    """A tensor op runs on CPU, and MPS is used when the Mac exposes it."""
    import torch

    device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")

    result = (torch.ones(4, 4, device=device) @ torch.ones(4, 4, device=device)).sum()

    # 4x4 of ones matmul'd gives every cell = 4, summed over 16 cells = 64.
    assert result.item() == pytest.approx(64.0)


@pytest.mark.unit
def test_flows_to_rows_convert_to_pyg_data():
    """FLOWS_TO rows map onto edge_index / edge_weight without losing edges."""
    rows = _flows_to_rows()
    data = _build_data(rows)

    # 5 edges, but only 4 distinct accounts: a, b, c, mule.
    assert data.num_nodes == 4
    assert data.edge_index.shape == (2, len(rows))
    assert data.x.shape == (4, NUM_FEATURES)
    assert data.edge_weight.shape == (len(rows),)

    # Direction must survive the mapping: acct_a -> acct_b, never the reverse.
    src, dst = data.edge_index[0][0].item(), data.edge_index[1][0].item()
    assert data.node_ids[src] == "acct_a"
    assert data.node_ids[dst] == "acct_b"

    # PyG validates edge_index bounds and dtype for us.
    data.validate(raise_on_error=True)


@pytest.mark.unit
def test_sageconv_forward_pass_returns_per_node_logits():
    """A forward pass emits one logit vector per account."""
    import torch

    data = _build_data(_flows_to_rows())
    model = _build_model()

    with torch.no_grad():
        out = model(data.x, data.edge_index)

    # One row per account, one column per risk level.
    assert out.shape == (data.num_nodes, NUM_CLASSES)
    assert torch.isfinite(out).all(), "logits must not contain NaN/inf"


@pytest.mark.unit
def test_sage_model_is_inductive_over_unseen_accounts():
    """The same weights score a graph containing accounts they never saw.

    This is the property CLAUDE.md relies on: SAGEConv learns aggregation
    functions over neighbourhoods rather than a fixed embedding per node, so
    a new account appearing mid-stream can be scored without retraining.
    A transductive model (plain GCN with learned per-node embeddings) could
    not do this.
    """
    import torch

    model = _build_model()
    small = _build_data(_flows_to_rows())

    # Two accounts that did not exist when the model was built.
    extended_rows = _flows_to_rows() + [
        ("acct_new_1", "acct_a", 900.0),
        ("acct_new_2", "acct_new_1", 750.0),
    ]
    large = _build_data(extended_rows)

    with torch.no_grad():
        small_out = model(small.x, small.edge_index)
        large_out = model(large.x, large.edge_index)

    assert small_out.shape == (4, NUM_CLASSES)
    assert large_out.shape == (6, NUM_CLASSES)
    assert torch.isfinite(large_out).all()

    # Weight shapes depend only on feature width, never on account count.
    assert model.conv1.lin_l.weight.shape[1] == NUM_FEATURES


@pytest.mark.unit
def test_smote_rebalances_imbalanced_node_features():
    """SMOTE evens out the fraud class on a node feature matrix.

    Fraud is rare, so the raw label distribution teaches a model that
    guessing "not fraud" every time is a great strategy. SMOTE synthesises
    minority rows by interpolating between neighbours, applied to feature
    vectors before training.
    """
    import numpy as np
    from imblearn.over_sampling import SMOTE

    rng = np.random.default_rng(0)
    # 200 legitimate accounts, 20 fraudulent — a 10:1 imbalance.
    legit = rng.normal(0.0, 1.0, size=(200, NUM_FEATURES))
    fraud = rng.normal(3.0, 1.0, size=(20, NUM_FEATURES))

    x = np.vstack([legit, fraud])
    y = np.concatenate([np.zeros(200, dtype=int), np.ones(20, dtype=int)])
    assert (y == 1).sum() == 20

    x_resampled, y_resampled = SMOTE(random_state=0, k_neighbors=5).fit_resample(x, y)

    # Both classes end up equally represented, and only the minority grew.
    assert (y_resampled == 0).sum() == (y_resampled == 1).sum() == 200
    assert x_resampled.shape[1] == NUM_FEATURES
    assert len(x_resampled) > len(x)
