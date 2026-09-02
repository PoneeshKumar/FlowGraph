"""Marked-account aggregation.

Pure functions, no I/O. Given a per-account view of the three detection signals
(GNN risk score, cycle membership, community-risk tier), decide whether the
account is *marked*, compute a combined score, and produce a written rationale
(a bare numeric score is never emitted — repo convention).
"""
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass(frozen=True)
class MarkWeights:
    gnn: float = 0.6
    cycle: float = 0.25
    community: float = 0.15


@dataclass(frozen=True)
class MarkThresholds:
    gnn: float = 0.5
    community_tiers: frozenset = field(
        default_factory=lambda: frozenset({"high", "critical"})
    )


def aggregate_account(
    account_id: str,
    gnn_score: Optional[float],
    in_cycle: bool,
    community_tier: Optional[str],
    weights: MarkWeights,
    thresholds: MarkThresholds,
) -> Optional[Dict]:
    """Return a mark record, or None if the account fires no signal.

    An account is marked if any of: GNN score >= threshold, membership in a
    detected cycle, or membership in a high/critical-risk community. The combined
    score is a weight-blended mean over *only the signals that fired*, so a lone
    signal scores as itself rather than being diluted by absent ones.
    """
    gnn_fired = gnn_score is not None and gnn_score >= thresholds.gnn
    cycle_fired = bool(in_cycle)
    community_fired = community_tier in thresholds.community_tiers

    if not (gnn_fired or cycle_fired or community_fired):
        return None

    parts = []  # (weight, value)
    if gnn_fired:
        parts.append((weights.gnn, float(gnn_score)))
    if cycle_fired:
        parts.append((weights.cycle, 1.0))
    if community_fired:
        parts.append((weights.community, 1.0))
    if len(parts) == 1:
        # A lone signal's combined score is exactly its own value — short-circuit
        # so the weight division can't introduce float error (0.552/0.6 != 0.92).
        combined = parts[0][1]
    else:
        wsum = sum(w for w, _ in parts)
        combined = sum(w * v for w, v in parts) / wsum

    fired = []
    if gnn_fired:
        fired.append(f"GNN risk {gnn_score:.2f}")
    if cycle_fired:
        fired.append("member of a detected cycle")
    if community_fired:
        fired.append(f"in a {community_tier}-risk community")
    rationale = f"Marked ({', '.join(fired)}); combined score {combined:.2f}."

    return {
        "account_id": account_id,
        "combined_score": combined,
        "signals": {
            "gnn": gnn_fired,
            "cycle": cycle_fired,
            "community": community_fired,
        },
        "gnn_score": gnn_score,
        "in_cycle": cycle_fired,
        "community_tier": community_tier,
        "rationale": rationale,
    }
