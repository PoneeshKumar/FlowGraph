"""Helpers for computing local weighted PageRank scores on account graphs."""

from typing import Dict, List


def compute_weighted_pagerank(
    adjacency: Dict[str, Dict[str, float]],
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
    if not adjacency:
        return {}

    nodes = sorted(adjacency.keys())
    for neighbors in adjacency.values():
        for node in neighbors:
            if node not in nodes:
                nodes.append(node)

    if not nodes:
        return {}

    scores = {node: 1.0 / len(nodes) for node in nodes}
    outgoing_weight_sums = {
        node: sum(neighbors.values()) for node, neighbors in adjacency.items()
    }

    for _ in range(max_iterations):
        new_scores = {
            node: (1.0 - damping) / len(nodes) for node in nodes
        }

        for node in nodes:
            incoming_contribution = 0.0
            for source, neighbors in adjacency.items():
                if node not in neighbors:
                    continue

                edge_weight = neighbors[node]
                outgoing_total = outgoing_weight_sums.get(source, 0.0)
                if outgoing_total > 0:
                    incoming_contribution += scores[source] * (
                        edge_weight / outgoing_total
                    )
                else:
                    incoming_contribution += scores[source] / len(nodes)

            new_scores[node] = (1.0 - damping) / len(nodes) + (
                damping * incoming_contribution
            )

        max_delta = max(
            abs(new_scores[node] - scores[node]) for node in nodes
        )
        scores = new_scores
        if max_delta < tolerance:
            break

    return scores
