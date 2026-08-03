"""Helpers for computing local weighted PageRank scores on account graphs."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Sequence, Tuple

import numpy as np


def _normalize_adjacency(adjacency: Mapping[str, Mapping[str, float]] | Sequence[tuple[str, Mapping[str, float]]]):
    """Normalize adjacency-like inputs into a deterministic mapping."""
    if adjacency is None:
        raise ValueError("adjacency cannot be None")

    # Duck-typed on .items() rather than isinstance(Mapping): a pandas Series
    # is mapping-LIKE and has .items(), but is not a Mapping subclass. Falling
    # through to list() would iterate its VALUES rather than (index, value)
    # pairs, and the unpack below would then fail on a perfectly valid input.
    if hasattr(adjacency, "items") and callable(adjacency.items):
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


def compute_pagerank_sparse(
    edges: Iterable[Tuple[str, str, float]],
    damping: float = 0.85,
    max_iterations: int = 100,
    tolerance: float = 1e-6,
) -> Dict[str, float]:
    """Weighted PageRank over a FULL graph, using a sparse transition matrix.

    compute_weighted_pagerank below allocates a dense node_count x node_count
    matrix. That is fine for the 2-hop neighbourhoods compute_local_pagerank
    feeds it, but it explodes on a whole graph: 515k accounts would need
    515088^2 * 8 bytes ~= 2.1 PB. It becomes unusable somewhere around 15k
    nodes. This version stores only the edges that exist, so memory scales with
    edge count instead of node count squared.

    Same maths as the dense version — row-normalized weights, uniform
    teleportation, sink mass redistributed evenly — so scores agree to
    numerical tolerance on inputs both can handle.

    scipy is imported lazily: it ships in requirements-ml.txt, and the Faust
    consumer imports this module transitively without installing that file.

    Args:
        edges:          (source, target, weight) triples. Duplicate pairs are
                        summed, matching how FLOWS_TO aggregates behave.
        damping:        Random-jump damping factor.
        max_iterations: Iteration cap. Higher default than the dense version
                        because whole graphs take longer to converge than
                        2-hop neighbourhoods.
        tolerance:      Convergence threshold on max score delta.

    Returns:
        Mapping of node -> PageRank score. Scores sum to ~1.0.
    """
    from scipy import sparse  # noqa: PLC0415 — deliberately lazy, see docstring

    if damping <= 0 or damping >= 1:
        raise ValueError("damping must be between 0 and 1")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")

    sources: list = []
    targets: list = []
    weights: list = []
    for edge in edges:
        try:
            source, target, weight = edge
        except (TypeError, ValueError) as exc:
            raise TypeError("edges must be (source, target, weight) triples") from exc
        if not isinstance(source, str) or not isinstance(target, str):
            raise TypeError("node identifiers must be strings")
        sources.append(source)
        targets.append(target)
        weights.append(float(weight))

    if not sources:
        return {}

    # Sorted for determinism, matching the dense implementation.
    nodes = sorted(set(sources) | set(targets))
    node_index = {node: idx for idx, node in enumerate(nodes)}
    node_count = len(nodes)

    row = np.fromiter((node_index[s] for s in sources), dtype=np.int64, count=len(sources))
    col = np.fromiter((node_index[t] for t in targets), dtype=np.int64, count=len(targets))
    data = np.asarray(weights, dtype=np.float64)

    # Negative weights would make row normalization meaningless.
    if np.any(data < 0):
        raise ValueError("edge weights must be non-negative")

    # csr_matrix sums duplicate (row, col) entries, which is what we want for
    # repeated account pairs.
    weight_matrix = sparse.csr_matrix(
        (data, (row, col)), shape=(node_count, node_count)
    )

    outgoing = np.asarray(weight_matrix.sum(axis=1)).ravel()
    has_out = outgoing > 0

    inverse = np.zeros(node_count, dtype=np.float64)
    inverse[has_out] = 1.0 / outgoing[has_out]
    transition = sparse.diags(inverse) @ weight_matrix

    # Transposed once up front: the iteration needs P^T @ scores every step.
    transition_t = transition.T.tocsr()

    scores = np.full(node_count, 1.0 / node_count, dtype=np.float64)
    sink_indices = np.flatnonzero(~has_out)
    teleportation = (1.0 - damping) / node_count

    for _ in range(max_iterations):
        incoming = transition_t @ scores
        if sink_indices.size:
            # Dangling nodes spread their mass uniformly, or it leaks away.
            incoming = incoming + float(scores[sink_indices].sum()) / node_count
        new_scores = teleportation + damping * incoming
        max_delta = float(np.max(np.abs(new_scores - scores)))
        scores = new_scores
        if max_delta < tolerance:
            break

    return {node: float(scores[idx]) for node, idx in node_index.items()}


def compute_weighted_pagerank(
    adjacency: Mapping[str, Mapping[str, float]] | Sequence[tuple[str, Mapping[str, float]]],
    damping: float = 0.85,
    max_iterations: int = 30,
    tolerance: float = 1e-6,
) -> Dict[str, float]:
    """Compute a weighted PageRank over a small directed adjacency map.

    Dense: allocates node_count^2 floats. Use compute_pagerank_sparse for
    anything bigger than a local neighbourhood.

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
