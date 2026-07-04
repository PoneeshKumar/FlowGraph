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


# ---------------------------------------------------------------------------
# Scoring (pure — no I/O, fully unit-testable)
# ---------------------------------------------------------------------------

def score_community(
    member_ids: List[str],
    internal_edge_count: int,
    internal_total_cents: int,
    flagged_member_count: int,
    conductance: float = 0.0,
    window_days: int = LOUVAIN_WINDOW_DAYS,
    density_ref: float = LOUVAIN_DENSITY_REF,
    volume_floor_cents: int = LOUVAIN_VOLUME_FLOOR_CENTS,
    volume_cap_cents: int = LOUVAIN_VOLUME_CAP_CENTS,
    overlap_ref: float = LOUVAIN_OVERLAP_REF,
    level_medium: float = LOUVAIN_LEVEL_MEDIUM,
    level_high: float = LOUVAIN_LEVEL_HIGH,
    level_critical: float = LOUVAIN_LEVEL_CRITICAL,
) -> Dict[str, Any]:
    """
    Score a detected community and produce a natural-language explanation.

    Five dimensions, mirroring the cycle scorer's shape:
      1. size band     — laundering rings run ~5–50 accounts; tiny communities are
                         below Louvain's resolution, huge ones are merchant hubs
      2. density       — internal edges / possible edges; meshes are dense,
                         benign hub-and-spoke stars are not
      3. volume        — log-scaled internal money movement in the window
      4. isolation     — 1 − conductance: an isolated cluster (money stays inside)
                         is more suspicious than a dense corner of an otherwise
                         well-connected legitimate hub. conductance is computed by
                         the caller (community_conductance / nx.conductance); 0.0
                         means no flow leaves the community → cohesion 1.0
      5. risk overlap  — fraction of members already flagged by OTHER detectors
                         (cross-detector signal; strongest single indicator)

    Args:
        member_ids:           community members (≥ 2)
        internal_edge_count:  undirected edges inside the community subgraph
        internal_total_cents: sum of total_amount over those edges
        conductance:          fraction of edge weight crossing the community
                              boundary, in [0, 1]; 0.0 = fully isolated
        flagged_member_count: members appearing in open risk_flags from other detectors

    Returns:
        {
          risk_score:  float in [0.0, 1.0]
          risk_level:  'low' | 'medium' | 'high' | 'critical'
          explanation: str  (always non-empty — regulatory requirement)
          details: dict     (raw numbers + per-dimension scores for audit trail)
        }
    """
    n = len(member_ids)
    if n < 2:
        raise ValueError("score_community needs at least 2 members")

    # --- 1. Size-band score ---
    if n <= 3:
        size_score = 0.2
    elif n <= 7:
        size_score = 0.7
    elif n <= 50:
        size_score = 1.0
    elif n <= 150:
        size_score = 0.5
    else:
        size_score = 0.1

    # --- 2. Density score ---
    possible_edges = n * (n - 1) / 2
    density = internal_edge_count / possible_edges if possible_edges else 0.0
    density_score = min(1.0, density / density_ref) if density_ref > 0 else 0.0

    # --- 3. Volume score (log scale, floor → 0.0, cap → 1.0) ---
    _floor = max(volume_floor_cents, 1)
    volume_score = min(
        1.0,
        math.log(max(internal_total_cents, _floor) / _floor + 1)
        / math.log(volume_cap_cents / _floor + 1),
    )

    # --- 4. Isolation / cohesion score ---
    # conductance is the fraction of edge weight crossing the boundary; a fully
    # isolated community (conductance 0) scores 1.0, a maximally leaky one 0.0.
    cohesion_score = max(0.0, 1.0 - min(1.0, conductance))

    # --- 5. Known-risk overlap score ---
    flagged_fraction = flagged_member_count / n
    overlap_score = min(1.0, flagged_fraction / overlap_ref) if overlap_ref > 0 else 0.0

    # --- Weighted composite ---
    # Overlap carries the most weight: corroboration from an independent
    # detector is stronger evidence than any topology feature alone.
    risk_score = (
        0.10 * size_score
        + 0.15 * density_score
        + 0.25 * volume_score
        + 0.15 * cohesion_score
        + 0.35 * overlap_score
    )
    risk_score = min(1.0, max(0.0, risk_score))

    if risk_score >= level_critical:
        risk_level = "critical"
    elif risk_score >= level_high:
        risk_level = "high"
    elif risk_score >= level_medium:
        risk_level = "medium"
    else:
        risk_level = "low"

    total_dollars = internal_total_cents / 100
    explanation = (
        f"Community of {n} accounts with {internal_edge_count} internal transfer "
        f"corridors (density {density:.0%}, boundary conductance {conductance:.2f}) "
        f"moved ${total_dollars:,.2f} internally within the last {window_days} days. "
        f"{flagged_member_count} member(s) already carry open risk flags from other "
        f"detectors. Risk score {risk_score:.2f} ({risk_level}). "
        f"Pattern consistent with a coordinated laundering network "
        f"(layering / smurfing cluster)."
    )

    details = {
        "n_members":            n,
        "internal_edge_count":  internal_edge_count,
        "internal_total_cents": internal_total_cents,
        "density":              round(density, 4),
        "conductance":          round(conductance, 4),
        "flagged_member_count": flagged_member_count,
        "size_score":           round(size_score, 4),
        "density_score":        round(density_score, 4),
        "volume_score":         round(volume_score, 4),
        "cohesion_score":       round(cohesion_score, 4),
        "overlap_score":        round(overlap_score, 4),
        "window_days":          window_days,
    }

    return {
        "risk_score":  round(risk_score, 4),
        "risk_level":  risk_level,
        "explanation": explanation,
        "details":     details,
    }
