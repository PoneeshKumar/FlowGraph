"""
Class-imbalance handling for the GNN, applied at the right point in the pipeline.

WHY THIS MODULE EXISTS — the SMOTE ordering problem
---------------------------------------------------
CLAUDE.md originally specified "SMOTE on node feature vectors before training".
That ordering does not work for a graph model, for a concrete reason:

SMOTE fabricates new rows by interpolating between existing ones. A fabricated
account has no edges — it appears nowhere in `edge_index`. But a GNN classifies
by message passing, i.e. by aggregating over a node's neighbours. A node with no
neighbours has nothing to aggregate, so synthetic rows either get silently
dropped or contribute only their own features, which is exactly the non-graph
signal the architecture exists to move beyond.

Three ways out:

  1. Interpolate AFTER the convolutions, on learned embeddings. By then each
     row already encodes its neighbourhood, so interpolating two of them is
     meaningful and no edges are required. This is what this module does.
  2. GraphSMOTE — synthesize edges alongside nodes, via a learned edge
     predictor. More faithful, considerably more machinery.
  3. Skip resampling and rely on Focal Loss plus per-class alpha, which need no
     synthetic data at all. Try this first: it is simpler, and it is the
     baseline that tells you whether (1) is buying anything.

A second trap, which is why this is hand-written rather than an imblearn call:
imblearn's SMOTE operates on numpy. Round-tripping embeddings through numpy
detaches them from the autograd graph, so no gradient flows back through the
conv layers from synthetic samples — the graph encoder stops learning from them
entirely. The interpolation here stays in torch and stays differentiable.

imbalanced-learn remains the right tool for flat tabular baselines (see
tests/test_gnn_stack.py); it is simply the wrong tool for this position.
"""

import logging
from typing import Optional, Tuple

import torch

logger = logging.getLogger(__name__)


def smote_embeddings(
    embeddings: torch.Tensor,
    labels: torch.Tensor,
    minority_classes: Optional[Tuple[int, ...]] = None,
    k_neighbours: int = 5,
    target_ratio: float = 1.0,
    generator: Optional[torch.Generator] = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """SMOTE-style oversampling on learned embeddings, differentiably.

    Call this on the output of the GNN's convolution layers, before the
    classification head — never on raw node features.

    For each synthetic sample: pick a minority embedding, pick one of its k
    nearest same-class neighbours, and interpolate at a random point on the
    segment between them:

        x_new = x_i + lam * (x_neighbour - x_i),   lam ~ U(0, 1)

    Because this is torch arithmetic on the live tensors, x_new keeps a path
    back to the conv weights and the encoder still learns from it.

    Args:
        embeddings:       [N, D] post-convolution embeddings.
        labels:           [N] int64 class indices.
        minority_classes: Which classes to oversample. Default: every class
                          with fewer members than the largest class.
        k_neighbours:     Neighbour pool per seed. Clamped to what the class
                          actually has, so a 2-member class still works.
        target_ratio:     Fraction of the majority count to raise each
                          minority class to. 1.0 = full balance.
        generator:        Optional torch.Generator for reproducibility.

    Returns:
        (augmented_embeddings, augmented_labels) — originals first, synthetic
        appended. Returns the inputs unchanged when there is nothing to do.
    """
    if embeddings.ndim != 2:
        raise ValueError(f"expected [N, D] embeddings, got {tuple(embeddings.shape)}")
    if labels.ndim != 1 or labels.shape[0] != embeddings.shape[0]:
        raise ValueError(
            f"labels shape {tuple(labels.shape)} does not match embeddings "
            f"shape {tuple(embeddings.shape)}"
        )
    if not 0.0 < target_ratio <= 1.0:
        raise ValueError(f"target_ratio must be in (0, 1], got {target_ratio}")
    if k_neighbours < 1:
        raise ValueError(f"k_neighbours must be >= 1, got {k_neighbours}")
    if embeddings.shape[0] == 0:
        return embeddings, labels

    present, counts = torch.unique(labels, return_counts=True)
    majority_count = int(counts.max())

    if minority_classes is None:
        targets = [
            int(c) for c, n in zip(present.tolist(), counts.tolist())
            if n < majority_count
        ]
    else:
        targets = [int(c) for c in minority_classes]

    if not targets:
        return embeddings, labels

    synthetic: list = []
    synthetic_labels: list = []

    for class_index in targets:
        member_idx = (labels == class_index).nonzero(as_tuple=True)[0]
        n_members = int(member_idx.numel())

        if n_members == 0:
            logger.debug("class %d absent — nothing to oversample", class_index)
            continue
        if n_members == 1:
            # Interpolation needs two distinct points. Duplicating the single
            # member would add no information and would inflate its influence,
            # so skip it and let Focal Loss carry this class.
            logger.warning(
                "class %d has 1 member; skipping SMOTE (needs >= 2 to "
                "interpolate). Focal Loss alpha will have to cover it.",
                class_index,
            )
            continue

        n_needed = int(round(majority_count * target_ratio)) - n_members
        if n_needed <= 0:
            continue

        member_embeddings = embeddings[member_idx]

        # Pairwise distances within the class, self excluded so a seed never
        # interpolates with itself (which would produce an exact duplicate).
        distances = torch.cdist(
            member_embeddings.detach(), member_embeddings.detach()
        )
        distances.fill_diagonal_(float("inf"))

        k = min(k_neighbours, n_members - 1)
        _, neighbour_idx = torch.topk(distances, k, dim=1, largest=False)

        seed_positions = torch.randint(
            0, n_members, (n_needed,), generator=generator, device=embeddings.device
        )
        neighbour_choice = torch.randint(
            0, k, (n_needed,), generator=generator, device=embeddings.device
        )
        chosen_neighbour = neighbour_idx[seed_positions, neighbour_choice]

        lam = torch.rand(
            (n_needed, 1), generator=generator, device=embeddings.device
        )

        seeds = member_embeddings[seed_positions]
        partners = member_embeddings[chosen_neighbour]
        # Differentiable: gradient flows to both endpoints.
        synthetic.append(seeds + lam * (partners - seeds))
        synthetic_labels.append(
            torch.full(
                (n_needed,), class_index, dtype=labels.dtype, device=labels.device
            )
        )

    if not synthetic:
        return embeddings, labels

    augmented = torch.cat([embeddings] + synthetic, dim=0)
    augmented_labels = torch.cat([labels] + synthetic_labels, dim=0)

    logger.info(
        "SMOTE on embeddings: %d -> %d samples (+%d synthetic)",
        embeddings.shape[0],
        augmented.shape[0],
        augmented.shape[0] - embeddings.shape[0],
    )
    return augmented, augmented_labels
