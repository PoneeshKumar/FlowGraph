"""Helpers for computing local weighted PageRank scores on account graphs."""

from __future__ import annotations

from typing import Dict, Mapping, Sequence

import numpy as np


def _normalize_adjacency(adjacency: Mapping[str, Mapping[str, float]] | Sequence[tuple[str, Mapping[str, float]]]):
    """Normalize adjacency-like inputs into a deterministic mapping."""
    if adjacency is None:
        raise ValueError("adjacency cannot be None")

    if hasattr(adjacency, "items") and not isinstance(adjacency, (str, bytes)):
        items = adjacency.items()
    else:
        try:
            items = list(adjacency)
        except TypeError as exc:
            raise TypeError("adjacency must be a mapping or iterable of (node, neighbors)") from exc

    normalized: Dict[str, Dict[str, float]] = {}
    for item in items:
        try:
            source, neighbors = item
        except (TypeError, ValueError) as exc:
            raise TypeError("adjacency entries must be (node, neighbors) pairs") from exc
        if not isinstance(source, str):
            raise TypeError("source node identifiers must be strings")
        if neighbors is None:
            normalized[source] = {}
            continue
        if not isinstance(neighbors, Mapping):
            raise TypeError("neighbor values must be mapping-like")
        normalized[source] = {str(target): float(weight) for target, weight in neighbors.items()}

    return normalized


def compute_weighted_pagerank(
    adjacency: Mapping[str, Mapping[str, float]] | Sequence[tuple[str, Mapping[str, float]]],
    damping: float = 0.85,
    max_iterations: int = 30,
    tolerance: float = 1e-6,
) -> Dict[str, float]:
    """Compute a weighted PageRank over a small directed adjacency map.

    Args:
        adjacency: Mapping from source node to a dict of outgoing edges and weights.
        damping: Damping factor for random jumps.
        max_iterations: Maximum number of iterations for convergence.
        tolerance: Convergence threshold for maximum score delta.

    Returns:
        Mapping of node -> PageRank score.
    """
    if damping <= 0 or damping >= 1:
        raise ValueError("damping must be between 0 and 1")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")

    normalized = _normalize_adjacency(adjacency)
    if not normalized:
        return {}

    nodes = sorted({*normalized.keys(), *(neighbor for neighbors in normalized.values() for neighbor in neighbors)})
    if not nodes:
        return {}

    node_index = {node: idx for idx, node in enumerate(nodes)}
    node_count = len(nodes)
    scores = np.full(node_count, 1.0 / node_count, dtype=np.float64)

    outgoing_weight_sums = np.zeros(node_count, dtype=np.float64)
    transition_matrix = np.zeros((node_count, node_count), dtype=np.float64)

    for source, neighbors in normalized.items():
        source_idx = node_index[source]
        total_weight = float(sum(neighbors.values())) if neighbors else 0.0
        outgoing_weight_sums[source_idx] = total_weight
        if total_weight <= 0:
            continue
        for target, weight in neighbors.items():
            target_idx = node_index[target]
            transition_matrix[source_idx, target_idx] = float(weight) / total_weight

    teleportation = (1.0 - damping) / node_count
    sink_sources = np.where(outgoing_weight_sums <= 0)[0]
    for _ in range(max_iterations):
        incoming = transition_matrix.T @ scores
        if sink_sources.size:
            incoming += np.full(node_count, float(scores[sink_sources].sum()) / node_count, dtype=np.float64)
        new_scores = np.full(node_count, teleportation, dtype=np.float64)
        new_scores += damping * incoming
        max_delta = float(np.max(np.abs(new_scores - scores)))
        scores = new_scores
        if max_delta < tolerance:
            break

    return {node: float(scores[idx]) for node, idx in node_index.items()}
