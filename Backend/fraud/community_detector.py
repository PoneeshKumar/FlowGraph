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
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

import networkx as nx
import numpy as np
import pandas as pd

from config import (
    LOUVAIN_WINDOW_DAYS,
    LOUVAIN_SEED,
    LOUVAIN_RESOLUTION,
    LOUVAIN_WEIGHT_MODE,
    LOUVAIN_MIN_COMMUNITY_SIZE,
    LOUVAIN_MIN_EDGE_TX_COUNT,
    LOUVAIN_EXPORT_TIMEOUT_SECONDS,
    LOUVAIN_CORE_K,
    LOUVAIN_DENSITY_REF,
    LOUVAIN_VOLUME_FLOOR_CENTS,
    LOUVAIN_VOLUME_CAP_CENTS,
    LOUVAIN_OVERLAP_REF,
    LOUVAIN_LEVEL_MEDIUM,
    LOUVAIN_LEVEL_HIGH,
    LOUVAIN_LEVEL_CRITICAL,
    LOUVAIN_ENGINE,
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


def _edge_weight_vectorized(
    total_amount_cents: pd.Series,
    tx_count: pd.Series,
    mode: str = LOUVAIN_WEIGHT_MODE,
) -> pd.Series:
    """Vectorized form of edge_weight — same semantics, applied column-wise."""
    if mode == "log_amount":
        return np.log1p(total_amount_cents.clip(lower=0))
    if mode == "amount":
        return total_amount_cents.clip(lower=0).astype(float)
    if mode == "tx_count":
        return tx_count.clip(lower=0).astype(float)
    if mode == "unweighted":
        return pd.Series(1.0, index=total_amount_cents.index)
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

    The directed→undirected merge is a groupby-sum over a pandas DataFrame
    rather than a per-edge Python dict accumulator — same result, vectorized
    over the full edge list instead of one Python-level op per record.

    Args:
        edges: dicts of {src, dst, total_amount, tx_count} from
               Neo4jClient.export_flows_to_edges
        weight_mode: see edge_weight

    Returns:
        nx.Graph with edge attributes: weight (float), total_amount (int), tx_count (int)
    """
    graph = nx.Graph()
    if not edges:
        return graph

    df = pd.DataFrame(edges, columns=["src", "dst", "total_amount", "tx_count"])
    df = df[df["src"] != df["dst"]]
    if df.empty:
        return graph

    df["weight"] = _edge_weight_vectorized(df["total_amount"], df["tx_count"], weight_mode)

    # Canonical unordered pair so A→B and B→A land in the same group.
    node_a = df[["src", "dst"]].min(axis=1)
    node_b = df[["src", "dst"]].max(axis=1)
    df = df.assign(node_a=node_a, node_b=node_b)

    agg = df.groupby(["node_a", "node_b"], sort=False, as_index=False).agg(
        weight=("weight", "sum"),
        total_amount=("total_amount", "sum"),
        tx_count=("tx_count", "sum"),
    )

    graph.add_edges_from(
        (row.node_a, row.node_b, {
            "weight": row.weight,
            "total_amount": int(row.total_amount),
            "tx_count": int(row.tx_count),
        })
        for row in agg.itertuples(index=False)
    )
    return graph


def filter_weak_edges(
    graph: nx.Graph,
    min_tx_count: int = LOUVAIN_MIN_EDGE_TX_COUNT,
) -> nx.Graph:
    """
    Drop edges whose combined tx_count falls below min_tx_count before Louvain
    partitions the graph.

    See LOUVAIN_MIN_EDGE_TX_COUNT in config.py for the rationale and the
    benchmark numbers this threshold was tuned against. Nodes that lose all
    their edges simply become isolated and never reach LOUVAIN_MIN_COMMUNITY_SIZE
    downstream — no separate cleanup needed.
    """
    if min_tx_count <= 1:
        return graph
    pruned = nx.Graph()
    pruned.add_nodes_from(graph.nodes)
    pruned.add_edges_from(
        (u, v, d) for u, v, d in graph.edges(data=True) if d["tx_count"] >= min_tx_count
    )
    return pruned


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
      2. density       — edges beyond the bare spanning-tree minimum (n-1),
                         relative to that minimum; a mesh has excess
                         connectivity, a chain glued together by Louvain
                         does not, at any community size
      3. volume        — log-scaled internal money movement in the window
      4. isolation     — 1 − conductance: an isolated cluster (money stays inside)
                         is more suspicious than a dense corner of an otherwise
                         well-connected legitimate hub. conductance is computed by
                         the caller (community_conductance / nx.conductance); 0.0
                         means no flow leaves the community → cohesion 1.0.
                         Tried gating this off when a community equals its entire
                         weakly-connected component (isolation is then structurally
                         guaranteed, not chosen) — reverted: on the IBM AML
                         benchmark, every single one of the true positives the
                         un-gated scorer found was ALSO a standalone component,
                         so gating wiped out real detections at the same rate
                         as noise (precision 1.23% -> 0.00%). Small real fraud
                         rings apparently need this signal to clear the medium
                         bar just as much as background noise does.
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
    # Raw density (internal_edge_count / all possible pairs) is kept for
    # context in the explanation, but scoring uses tree_excess_ratio: edges
    # beyond the bare minimum (n-1) needed to stay connected, relative to
    # that minimum. Raw density collapses toward 0 for any large n regardless
    # of shape — a 2,126-node bare spanning tree has density 0.001%, making it
    # numerically indistinguishable from truly sparse noise at that scale, so
    # it couldn't tell a mesh from a chain once communities got past a few
    # dozen members (see git history: a min-tx-count edge filter was tried
    # first and reverted — it cut real fan-in/fan-out rings along with noise
    # because both rely on single-transaction edges). tree_excess_ratio is
    # scale-invariant instead: a bare spanning tree scores exactly 0 at any n,
    # a complete graph scores ~n/2 — "how much extra connectivity beyond
    # merely-connected" is the actual suspicious signal, not a fraction of a
    # quadratic ceiling almost nothing reaches.
    possible_edges = n * (n - 1) / 2
    density = internal_edge_count / possible_edges if possible_edges else 0.0
    min_edges_for_connectivity = max(n - 1, 1)
    tree_excess_ratio = max(0.0, internal_edge_count - min_edges_for_connectivity) / min_edges_for_connectivity
    density_score = min(1.0, tree_excess_ratio / density_ref) if density_ref > 0 else 0.0

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
        "tree_excess_ratio":    round(tree_excess_ratio, 4),
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


# ---------------------------------------------------------------------------
# Connectivity + conductance helpers (pure — operate on the built graph)
# ---------------------------------------------------------------------------

def community_conductance(
    graph: nx.Graph,
    members: Iterable[str],
    node_weighted_degree: Optional[Dict[str, float]] = None,
    total_weighted_degree: Optional[float] = None,
) -> float:
    """
    Fraction of a community's edge weight that crosses its boundary.

    Low = an isolated cluster money stays inside (suspicious); high = a dense
    sub-region of otherwise-normal traffic. Returns 0.0 for a community that
    spans the whole graph — there is no boundary.

    cut(S) = vol(S) - 2*internal_weight(S); conductance = cut(S) /
    min(vol(S), vol(complement)). Mathematically identical to
    nx.conductance(graph, S, weight="weight") (verified directly against it)
    but O(|members|) once degree is known, instead of nx.conductance's cost —
    this is called once per kept community, thousands of times per batch run,
    and the difference is the gap between a run finishing in seconds versus
    not finishing inside a reasonable batch window. A caller scoring many
    communities against the same graph should precompute
    node_weighted_degree (= dict(graph.degree(weight="weight"))) and
    total_weighted_degree (= sum of its values) once and pass them in;
    otherwise they're computed fresh here every call.
    """
    member_set = set(members)
    if len(member_set) >= graph.number_of_nodes():
        return 0.0

    if node_weighted_degree is None:
        node_weighted_degree = dict(graph.degree(weight="weight"))
    if total_weighted_degree is None:
        total_weighted_degree = sum(node_weighted_degree.values())

    vol_s = sum(node_weighted_degree[m] for m in member_set)
    internal_weight = sum(
        d["weight"] for _, _, d in graph.subgraph(member_set).edges(data=True)
    )
    cut = vol_s - 2 * internal_weight
    vol_complement = total_weighted_degree - vol_s
    denom = min(vol_s, vol_complement)
    return 0.0 if denom <= 0 else max(0.0, min(1.0, cut / denom))


def split_disconnected(
    communities: Iterable[Iterable[str]],
    graph: nx.Graph,
) -> List[Set[str]]:
    """
    Split any internally-disconnected community into its connected components.

    Louvain can assign nodes with no path between them to the same community — a
    documented modularity-optimization defect (fixed by construction under the
    Leiden engine). A disconnected 'community' would corrupt core_members and the
    fingerprint identity, so we break it into genuinely-connected pieces before
    scoring. Under Leiden this is a cheap no-op (communities are already connected).
    """
    result: List[Set[str]] = []
    for community in communities:
        members = set(community)
        sub = graph.subgraph(members)
        if sub.number_of_nodes() <= 1 or nx.is_connected(sub):
            result.append(members)
        else:
            for component in nx.connected_components(sub):
                result.append(set(component))
    return result


# ---------------------------------------------------------------------------
# Engine dispatch (networkx Louvain default; optional leidenalg Leiden)
# ---------------------------------------------------------------------------

def partition_graph(
    graph: nx.Graph,
    engine: str = LOUVAIN_ENGINE,
    resolution: float = LOUVAIN_RESOLUTION,
    seed: int = LOUVAIN_SEED,
) -> List[Set[str]]:
    """
    Partition an undirected weighted graph into communities.

    engine="networkx" (default): pure-Python networkx Louvain, zero extra deps.
    engine="leiden": leidenalg over igraph — C/C++ core, faster on large graphs,
      communities internally connected by construction (the caller still runs
      split_disconnected defensively, which is then a no-op).

    The networkx→igraph conversion the leiden path adds is a single O(E) pass and
    is dwarfed by the clustering; both engines share the same real ceiling (the
    exported edge list fitting in Python memory).
    """
    if engine == "networkx":
        return [
            set(c)
            for c in nx.community.louvain_communities(
                graph, weight="weight", resolution=resolution, seed=seed
            )
        ]
    if engine == "leiden":
        return _leiden_partition(graph, resolution=resolution, seed=seed)
    raise ValueError(f"unknown LOUVAIN_ENGINE: {engine!r}")


def _leiden_partition(graph: nx.Graph, resolution: float, seed: int) -> List[Set[str]]:
    """
    Leiden community detection via leidenalg/igraph. Imported lazily so the
    default networkx engine never requires the optional GPL dependencies.

    build_undirected_graph never produces isolated nodes (every node comes from
    an edge), so TupleList captures all of them; the empty-graph guard covers the
    no-edge case for symmetry with the networkx path.
    """
    import igraph as ig
    import leidenalg as la

    if graph.number_of_edges() == 0:
        return [{n} for n in graph.nodes]

    g_ig = ig.Graph.TupleList(
        ((u, v, d["weight"]) for u, v, d in graph.edges(data=True)),
        weights=True,
    )
    partition = la.find_partition(
        g_ig,
        la.RBConfigurationVertexPartition,  # modularity + resolution — the louvain_communities analog
        weights="weight",
        resolution_parameter=resolution,
        seed=seed,
    )
    return [set(g_ig.vs[idx]["name"] for idx in community) for community in partition]


# ---------------------------------------------------------------------------
# Detector (requires live Neo4j + Postgres connections)
# ---------------------------------------------------------------------------

class CommunityDetector:
    """
    Runs the daily Louvain batch: export → cluster → score → persist.

    Args:
        neo4j_client:    Initialized Neo4jClient (db.neo4j)
        postgres_client: Initialized PostgresClient (db.postgres), or None to
                         compute without persisting flags / overlap lookups
    """

    def __init__(self, neo4j_client: Any, postgres_client: Any) -> None:
        self.neo4j = neo4j_client
        self.postgres = postgres_client

    async def run(
        self,
        reference_time: Optional[datetime] = None,
        export_timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        One full batch pass.

        Steps:
        1. Export FLOWS_TO edges active within LOUVAIN_WINDOW_DAYS
        2. Build the undirected weighted graph, drop edges under
           LOUVAIN_MIN_EDGE_TX_COUNT (weak-edge filter), run seeded Louvain
        3. Split any internally-disconnected community into connected components
        4. Drop communities under LOUVAIN_MIN_COMMUNITY_SIZE (noise)
        5. Fingerprint each community on its top-K core; community_id = fp[:12]
        6. Score 5-dimensionally (isolation via conductance; overlap uses flags
           from OTHER detectors)
        7. Persist flags scoring >= LOUVAIN_LEVEL_MEDIUM to risk_flags
        8. Write community_id node properties for ALL kept communities

        Args:
            reference_time: Window anchor / detected_at timestamp; defaults to
                            now (benchmarks pass the dataset's max timestamp)
            export_timeout_seconds:
                            Timeout for the FLOWS_TO export. Defaults to
                            LOUVAIN_EXPORT_TIMEOUT_SECONDS (120s), which is
                            sized for a modest window and is NOT enough for a
                            full-size graph: exporting the 1,010,384 edges of
                            the loaded HI-Small dataset takes over two minutes,
                            so the default aborts the batch before Louvain even
                            starts. Pass a larger value for bulk graphs.

        Returns:
            {"communities": kept count, "assignments": node props written,
             "flags": list of flag dicts (persisted ones when postgres present)}
        """
        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)
        timeout = (
            export_timeout_seconds
            if export_timeout_seconds is not None
            else LOUVAIN_EXPORT_TIMEOUT_SECONDS
        )

        edges = await self.neo4j.export_flows_to_edges(
            window_days=LOUVAIN_WINDOW_DAYS,
            reference_time=ref,
            query_timeout_seconds=timeout,
        )
        graph = build_undirected_graph(edges)
        if graph.number_of_nodes() == 0:
            logger.info("Louvain batch: no active FLOWS_TO edges in window — nothing to do")
            return {"communities": 0, "assignments": 0, "flags": []}
        graph = filter_weak_edges(graph)

        raw_communities = partition_graph(graph)
        # Guarantee each community is internally connected before it earns an
        # identity/fingerprint (Louvain can emit disconnected communities).
        communities = split_disconnected(raw_communities, graph)

        # Precomputed once and reused by community_conductance for every kept
        # community below — see that function's docstring for why this matters.
        node_weighted_degree = dict(graph.degree(weight="weight"))
        total_weighted_degree = sum(node_weighted_degree.values())

        flagged_accounts: Set[str] = set()
        if self.postgres is not None:
            flagged_accounts = set(
                await self.postgres.get_flagged_account_ids(
                    status="open", exclude_flag_type="COMMUNITY"
                )
            )

        assignments: Dict[str, str] = {}
        flags: List[Dict[str, Any]] = []
        kept = 0

        for community in communities:
            members = sorted(community)
            if len(members) < LOUVAIN_MIN_COMMUNITY_SIZE:
                continue
            kept += 1

            core = core_members(graph, members)
            fingerprint = community_fingerprint(core)
            community_id = fingerprint[:12]
            for member in members:
                assignments[member] = community_id

            sub = graph.subgraph(members)
            internal_total = sum(
                attrs["total_amount"] for _, _, attrs in sub.edges(data=True)
            )
            scored = score_community(
                member_ids=members,
                internal_edge_count=sub.number_of_edges(),
                internal_total_cents=internal_total,
                flagged_member_count=len(flagged_accounts & set(members)),
                conductance=community_conductance(
                    graph, members, node_weighted_degree, total_weighted_degree
                ),
            )

            if scored["risk_score"] < LOUVAIN_LEVEL_MEDIUM:
                continue

            scored["details"]["community_id"] = community_id
            scored["details"]["core_members"] = core

            if self.postgres is not None:
                await self.postgres.upsert_risk_flag(
                    flag_type="COMMUNITY",
                    fingerprint=fingerprint,
                    account_ids=members,
                    risk_level=scored["risk_level"],
                    risk_score=scored["risk_score"],
                    explanation=scored["explanation"],
                    details=scored["details"],
                )

            flags.append({
                "fingerprint":  fingerprint,
                "community_id": community_id,
                "account_ids":  members,
                **scored,
            })
            logger.info(
                "Community flag | level=%s score=%.2f members=%d id=%s",
                scored["risk_level"], scored["risk_score"], len(members), community_id,
            )

        written = await self.neo4j.write_community_assignments(
            assignments, detected_at_epoch=int(ref.timestamp())
        )

        logger.info(
            "Louvain batch done | communities=%d (of %d raw) | assignments=%d | flags=%d",
            kept, len(communities), written, len(flags),
        )
        return {"communities": kept, "assignments": written, "flags": flags}


# ---------------------------------------------------------------------------
# Manual entrypoint: seeds a gather-scatter cluster and runs detection
# ---------------------------------------------------------------------------

async def _run_demo() -> None:
    """
    Inject a known gather-scatter community into Neo4j and run the batch.
    Four sources funnel ~$40k each into a collector, which scatters to three
    mules — 8 accounts, 7 corridors, the classic smurfing shape that cycle
    detection cannot see. Requires docker compose up (Postgres + Neo4j).

    Usage:
        python -m fraud.community_detector
    """
    import pathlib
    import sys
    from datetime import timedelta

    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

    from db.neo4j import Neo4jClient
    from db.postgres import PostgresClient

    logging.basicConfig(level=logging.INFO)

    neo4j_client = Neo4jClient()
    postgres_client = PostgresClient()

    await neo4j_client.initialize()
    await postgres_client.initialize()
    await neo4j_client.init_constraints()

    # Ensure the risk_flags table exists
    migration_sql = (
        pathlib.Path(__file__).parent.parent
        / "migrations"
        / "002_create_risk_flags_table.sql"
    ).read_text()
    async with postgres_client._get_connection() as conn:
        await conn.execute(migration_sql)

    now = datetime.now(timezone.utc)
    hops = [
        # Gather: four sources → collector
        ("DEMO_LV_SRC1", "DEMO_LV_HUB", 4_100_000, 0),
        ("DEMO_LV_SRC2", "DEMO_LV_HUB", 3_900_000, 1800),
        ("DEMO_LV_SRC3", "DEMO_LV_HUB", 4_050_000, 3600),
        ("DEMO_LV_SRC4", "DEMO_LV_HUB", 3_950_000, 5400),
        # Scatter: collector → three mules
        ("DEMO_LV_HUB", "DEMO_LV_MULE1", 5_200_000, 86_400),
        ("DEMO_LV_HUB", "DEMO_LV_MULE2", 5_300_000, 90_000),
        ("DEMO_LV_HUB", "DEMO_LV_MULE3", 5_100_000, 93_600),
    ]

    print("Seeding gather-scatter demo cluster …")
    for i, (src, dst, amount, offset_s) in enumerate(hops):
        ts = now - timedelta(days=2) + timedelta(seconds=offset_s)
        await neo4j_client.upsert_transaction_graph(
            sender_id=src,
            receiver_id=dst,
            amount_cents=amount,
            timestamp_utc=ts,
            rail="WIRE",
            event_type="SETTLEMENT",
            transaction_id=f"txn-demo-lv-{i}",
            idempotency_key=f"txn-demo-lv-{i}",
        )
        print(f"  {src} → {dst} | ${amount/100:,.2f}")

    print("\nRunning Louvain batch …")
    detector = CommunityDetector(neo4j_client, postgres_client)
    result = await detector.run()

    print(f"\nCommunities kept: {result['communities']}")
    print(f"Node assignments written: {result['assignments']}")
    for flag in result["flags"]:
        print(f"\n  COMMUNITY flag {flag['community_id']}")
        print(f"  level={flag['risk_level']} score={flag['risk_score']}")
        print(f"  members: {flag['account_ids']}")
        print(f"  {flag['explanation']}")
    if not result["flags"]:
        print("\n  (no community cleared the medium threshold)")

    await neo4j_client.close()
    await postgres_client.close()


if __name__ == "__main__":
    import asyncio as _asyncio

    _asyncio.run(_run_demo())
