"""
Louvain Community Detection Fraud Engine for FlowGraph.

Daily batch: partitions the aggregate FLOWS_TO graph into communities and flags
clusters whose shape matches coordinated laundering (gather-scatter, bipartite,
stacks) — structures invisible to per-path cycle detection. Each flagged
community is:
  1. Fingerprinted on its top-K weighted-degree core (stable under peripheral churn)
  2. Scored across five dimensions (size band, density, internal volume,
     isolation/conductance, known-risk overlap with flags from other detectors)
  3. Given a written risk-level explanation (regulatory requirement)
  4. Persisted into risk_flags via postgres.upsert_risk_flag (idempotent on fingerprint)

Every kept community (flagged or not) also has its membership written back to
Neo4j as Account.community_id / Account.community_detected_at node properties,
so subgraph queries and AI enrichment get community context.

Standalone usage:
  python -m fraud.community_detector   [seeds a gather-scatter demo and runs detection]

Not wired into a scheduler — the daily cadence is a deploy concern (cron), a
documented follow-up like the cycle detector's live wiring.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Dict, Iterable, List

import networkx as nx

from config import (
    LOUVAIN_WINDOW_DAYS,
    LOUVAIN_SEED,
    LOUVAIN_RESOLUTION,
    LOUVAIN_WEIGHT_MODE,
    LOUVAIN_MIN_COMMUNITY_SIZE,
    LOUVAIN_CORE_K,
    LOUVAIN_DENSITY_REF,
    LOUVAIN_VOLUME_FLOOR_CENTS,
    LOUVAIN_VOLUME_CAP_CENTS,
    LOUVAIN_OVERLAP_REF,
    LOUVAIN_LEVEL_MEDIUM,
    LOUVAIN_LEVEL_HIGH,
    LOUVAIN_LEVEL_CRITICAL,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph construction (pure — no I/O, fully unit-testable)
# ---------------------------------------------------------------------------

def edge_weight(
    total_amount_cents: int,
    tx_count: int,
    mode: str = LOUVAIN_WEIGHT_MODE,
) -> float:
    """
    Modularity weight for one directed FLOWS_TO record.

    "log_amount" is the default: value-aware but whale-dampened, so a single
    large legitimate payment (payroll, settlement) cannot dominate community
    structure the way it would under raw amounts.
    """
    if mode == "log_amount":
        return math.log1p(max(total_amount_cents, 0))
    if mode == "amount":
        return float(max(total_amount_cents, 0))
    if mode == "tx_count":
        return float(max(tx_count, 0))
    if mode == "unweighted":
        return 1.0
    raise ValueError(f"unknown LOUVAIN_WEIGHT_MODE: {mode!r}")


def build_undirected_graph(
    edges: List[Dict[str, Any]],
    weight_mode: str = LOUVAIN_WEIGHT_MODE,
) -> nx.Graph:
    """
    Collapse directed FLOWS_TO records into an undirected weighted graph.

    Louvain optimizes undirected modularity, so A→B and B→A merge into one
    edge: weights sum (computed per directed record, then added), and the raw
    total_amount / tx_count aggregates sum too — the scorer reads those.

    Self-loops are dropped: an account transferring to itself carries no
    community signal and networkx modularity treats loops inconsistently.

    Args:
        edges: dicts of {src, dst, total_amount, tx_count} from
               Neo4jClient.export_flows_to_edges
        weight_mode: see edge_weight

    Returns:
        nx.Graph with edge attributes: weight (float), total_amount (int), tx_count (int)
    """
    graph = nx.Graph()
    for e in edges:
        src, dst = e["src"], e["dst"]
        if src == dst:
            continue
        w = edge_weight(e["total_amount"], e["tx_count"], weight_mode)
        if graph.has_edge(src, dst):
            attrs = graph[src][dst]
            attrs["weight"] += w
            attrs["total_amount"] += e["total_amount"]
            attrs["tx_count"] += e["tx_count"]
        else:
            graph.add_edge(
                src, dst,
                weight=w,
                total_amount=e["total_amount"],
                tx_count=e["tx_count"],
            )
    return graph


# ---------------------------------------------------------------------------
# Community identity (pure)
# ---------------------------------------------------------------------------

def core_members(
    graph: nx.Graph,
    members: Iterable[str],
    k: int = LOUVAIN_CORE_K,
) -> List[str]:
    """
    The K most-connected members of a community, by weighted degree *within*
    the community subgraph. Ties break lexicographically so the result — and
    the fingerprint built on it — is deterministic.

    The core is what stays stable across daily runs while peripheral accounts
    churn in and out, so it anchors the community's identity.
    """
    sub = graph.subgraph(members)
    ranked = sorted(sub.nodes, key=lambda n: (-sub.degree(n, weight="weight"), n))
    return sorted(ranked[:k])


def community_fingerprint(core: Iterable[str]) -> str:
    """
    Stable unique key for a community: sha256 of the sorted core member ids.

    Same core tomorrow → same fingerprint → upsert_risk_flag bumps
    detection_count instead of spawning a duplicate alert. Blindspot (accepted
    in design review): if the core itself splits or merges, a new flag is born.
    """
    ids = sorted(core)
    if not ids:
        raise ValueError("community_fingerprint: empty core")
    return hashlib.sha256("|".join(ids).encode()).hexdigest()
