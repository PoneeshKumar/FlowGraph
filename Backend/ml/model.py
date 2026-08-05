"""
GraphSAGE risk classifier.

Split deliberately into an encoder and a classification head:

    h      = encode(x, edge_index)   # post-convolution node embeddings
    logits = classify(h)

That seam is what makes embedding-level SMOTE possible (ml/imbalance.py).
Interpolating raw node features produces rows with no edges, which cannot
participate in message passing at all; interpolating `h` is meaningful because
each row already encodes its neighbourhood.

Inductive by construction: SAGEConv learns aggregation functions rather than a
per-node embedding table, so weight shapes depend on feature WIDTH and never on
node COUNT. A new account appearing mid-stream is scored with existing weights
and no retraining — the property the whole streaming design rests on.

Depth is 2 by default. Each layer is one hop, so 2 layers means every account is
represented by its own features plus a learned summary of its 2-hop
neighbourhood. Stacking more layers to "see further" backfires: deep GNNs
oversmooth, with every node's representation converging toward the same average.
Long-range structure is the cycle detector's job, and it arrives here as a
feature.

DIRECTIONALITY
--------------
FLOWS_TO is directed src->dst, so a plain SAGEConv aggregates only a node's
PAYERS (in-neighbours). On the loaded HI-Small graph 45% of accounts have no
in-neighbour at all — overwhelmingly late-appearing senders — so message passing
contributes nothing to them, and FAN-IN vs FAN-OUT (mirror images) look
identical. `bidirectional=True` adds a second SAGEConv stream over the reversed
edges (a node's PAYEES) and concatenates the two per layer, so every account
sees both sides of its flow. Measured on the temporal split: +validation PR-AUC
and higher test ROC-AUC (0.94 -> 0.97) than the single-direction model.
"""

import logging
from typing import List, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import SAGEConv

logger = logging.getLogger(__name__)


def pick_device(preference: str = "auto") -> torch.device:
    """Choose a compute device.

    'auto' prefers CUDA, then MPS (Apple Silicon), then CPU. MPS is worth having
    but is not always faster for sparse scatter ops at this scale, so benchmark
    before assuming.
    """
    if preference != "auto":
        return torch.device(preference)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class GraphSAGERiskClassifier(nn.Module):
    """SAGEConv stack + linear head for per-account risk classification.

    Args:
        in_channels:  Feature width (FeatureSet.num_features).
        hidden:       Hidden width per layer.
        num_classes:  Output logits. 2 for laundering / not.
        num_layers:   SAGEConv layers = hops of neighbourhood seen.
        dropout:      Applied between layers and before the head.
        aggr:         Neighbour aggregation: 'mean', 'max' or 'sum'. 'mean' is
                      the safe default here — 'sum' would let a hub with 14,775
                      neighbours swamp everything downstream.
    """

    def __init__(
        self,
        in_channels: int,
        hidden: int = 64,
        num_classes: int = 2,
        num_layers: int = 2,
        dropout: float = 0.3,
        aggr: str = "mean",
        bidirectional: bool = False,
    ) -> None:
        super().__init__()
        if num_layers < 1:
            raise ValueError(f"num_layers must be >= 1, got {num_layers}")
        if not 0.0 <= dropout < 1.0:
            raise ValueError(f"dropout must be in [0, 1), got {dropout}")

        self.num_layers = num_layers
        self.dropout = dropout
        self.bidirectional = bidirectional

        # In bidirectional mode a second stream aggregates the reversed edges
        # (a node's payees); the two per-layer outputs are concatenated, so the
        # running width doubles. See the module docstring for why.
        self.convs = nn.ModuleList()
        self.convs_rev = nn.ModuleList() if bidirectional else None
        width = in_channels
        for _ in range(num_layers):
            self.convs.append(SAGEConv(width, hidden, aggr=aggr))
            if bidirectional:
                self.convs_rev.append(SAGEConv(width, hidden, aggr=aggr))
                width = hidden * 2
            else:
                width = hidden

        # Kept separate from the convolutions so encode() can be called on its
        # own — see the module docstring.
        self.head = nn.Linear(width, num_classes)

    def encode(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """Run the convolutions, returning post-convolution embeddings.

        Width is `hidden`, or `2 * hidden` in bidirectional mode. No activation
        or dropout after the final conv: SMOTE and the head both want the clean
        representation.
        """
        if self.bidirectional:
            # flip(0) turns src->dst into dst->src, so this stream aggregates a
            # node's payees rather than its payers.
            rev = edge_index.flip(0)
            for i in range(self.num_layers):
                h_fwd = self.convs[i](x, edge_index)
                h_rev = self.convs_rev[i](x, rev)
                x = torch.cat([h_fwd, h_rev], dim=-1)
                if i < self.num_layers - 1:
                    x = F.relu(x)
                    x = F.dropout(x, p=self.dropout, training=self.training)
            return x

        for i, conv in enumerate(self.convs):
            x = conv(x, edge_index)
            if i < self.num_layers - 1:
                x = F.relu(x)
                x = F.dropout(x, p=self.dropout, training=self.training)
        return x

    def classify(self, h: torch.Tensor) -> torch.Tensor:
        """Map embeddings to raw logits. No softmax — Focal Loss applies it."""
        h = F.relu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)
        return self.head(h)

    def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        return self.classify(self.encode(x, edge_index))

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def describe(self) -> List[str]:
        lines = [
            f"layers      : {self.num_layers} SAGEConv "
            f"({self.num_layers} hops of neighbourhood)",
            f"direction   : {'bidirectional (payers + payees)' if self.bidirectional else 'in-neighbours only'}",
            f"hidden      : {self.convs[0].out_channels}",
            f"dropout     : {self.dropout}",
            f"parameters  : {self.num_parameters():,}",
        ]
        return lines


@torch.no_grad()
def predict_scores(
    model: nn.Module,
    x: torch.Tensor,
    edge_index: torch.Tensor,
    positive_class: int = 1,
) -> torch.Tensor:
    """P(positive_class) per node, for threshold-free ranking metrics."""
    model.eval()
    logits = model(x, edge_index)
    return torch.softmax(logits.float(), dim=-1)[:, positive_class]
