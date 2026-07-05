# Louvain Community Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily-batch Louvain community detection over the aggregate `FLOWS_TO` graph that flags suspicious money-movement clusters (gather-scatter, scatter-gather, bipartite, stack — structures the cycle detector cannot see) into the shared `risk_flags` store.

**Architecture:** A new `fraud/community_detector.py` module mirrors the proven `fraud/cycle_detector.py` shape: pure, I/O-free scoring/fingerprinting functions + a `CommunityDetector` class that orchestrates Neo4j export → community partition → connectivity split → 5-dimension scoring → `risk_flags` upsert. Community assignments are batch-written back onto Neo4j `Account` nodes as `community_id` properties. Partitioning runs Python-side behind a `LOUVAIN_ENGINE` knob: `networkx.community.louvain_communities` by default (no extra dependency), or `leidenalg`/`igraph` Leiden as an opt-in engine that guarantees internally-connected communities and scales better on large graphs. An IBM AML benchmark runner validates recall/precision against the labeled non-cycle typologies, reusing the existing `benchmarks/ibm_aml` harness.

**Tech Stack:** Python 3.9, networkx 3.2.1 (last version supporting Py3.9), neo4j 5.21.0 async driver, asyncpg, pytest + pytest-asyncio. Optional Leiden engine: `igraph` 1.0.0 + `leidenalg` 0.12.0 (both ship Py3.9 macOS-arm64 wheels; GPL-licensed).

## Global Constraints

- Python 3.9.6 — do NOT use `match`, `X | Y` type unions at runtime, or networkx ≥ 3.3 (requires Py3.10). Every module uses `from __future__ import annotations`.
- Pin `networkx==3.2.1` in `requirements.txt`.
- All Cypher runtime values use named parameters, never string interpolation (CLAUDE.md convention).
- All time values stored/compared in UTC; Neo4j timestamps are **unix epoch seconds (int)**, matching `FLOWS_TO.first_ts/last_ts`.
- Every persisted risk flag carries a non-empty `explanation` (regulatory requirement — `upsert_risk_flag` raises otherwise).
- All tunables are env-var config knobs in `config.py` with documented defaults, following the `CYCLE_*` style.
- New flag discriminator value: `flag_type='COMMUNITY'` (the `risk_flags` table anticipates this via its `flag_type` column).
- Amounts are integer **cents** everywhere.
- Pytest markers: pure-logic tests marked `@pytest.mark.unit`, real-Neo4j tests marked `@pytest.mark.integration` and skipped when Neo4j is unreachable (pattern from `tests/test_neo4j_cycle_integration.py`).
- Node properties written by the batch: `community_id` (12-hex-char string), `community_detected_at` (epoch seconds int). No new node *types* — only properties on existing `Account` nodes.
- The optional Leiden engine uses `igraph==1.0.0` + `leidenalg==0.12.0` (PyPI package is `igraph`, **not** the deprecated `python-igraph`). Both are **GPL-3.0** — acceptable for an internal/backend service, but the default engine stays networkx so the GPL dependency is opt-in, never forced. The default install path adds no GPL code.

## Decisions locked during design review (2026-07-03)

| Decision | Choice |
|---|---|
| Branch base | Reset `louvain-clustering` onto `cycle-detection` (FLOWS_TO, risk_flags, fraud/, benchmark harness all live there) |
| Engine | Python-side, seeded, behind `LOUVAIN_ENGINE`: `networkx.community.louvain_communities` (default) or `leidenalg` Leiden (opt-in, connected-community guarantee, better scaling) |
| Edge weight | `log1p(total_amount)`, configurable via `LOUVAIN_WEIGHT_MODE` |
| Graph scope | Edges with `last_ts` in a 30-day window (`LOUVAIN_WINDOW_DAYS`, tuning knob for benchmark) |
| Persistence | Neo4j node props for all kept communities + `risk_flags` rows for communities scoring ≥ medium |
| Scoring | 5D: size band + density + internal volume + isolation (1−conductance) + known-risk overlap (cross-detector signal) |
| Connectivity | Every partitioned community is split into connected components before scoring (fixes Louvain's disconnected-community defect; a no-op under Leiden) |
| Fingerprint | sha256 of top-K (10) weighted-degree core members — stable under peripheral churn |
| Trigger | Manual entrypoint `python -m fraud.community_detector`; scheduling is a documented follow-up |
| Validation | IBM AML benchmark runner against labeled non-cycle typologies |

**Prior-art corroboration (research, 2026-07-04):** This Louvain-then-score architecture matches GARG-AML (arXiv 2506.04292), which partitions with Louvain then scores intra-community block density on the same IBM AML dataset. Combining community structure with an independent per-account signal (our known-risk overlap dimension) is the norm in the literature, not an exception. Isolation/conductance and connectivity-splitting were added after research flagged them as precedented gaps in the original 4D design.

## File structure

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | Add `networkx==3.2.1`; optional `igraph==1.0.0` + `leidenalg==0.12.0` (Leiden engine) |
| `config.py` | Modify | `LOUVAIN_*` knobs |
| `fraud/community_detector.py` | Create | Pure functions (weight, graph build, core, fingerprint, scoring, conductance, connectivity split, engine dispatch) + `CommunityDetector` + demo entrypoint |
| `db/neo4j.py` | Modify | `export_flows_to_edges`, `write_community_assignments` |
| `db/postgres.py` | Modify | `get_flagged_account_ids` |
| `tests/test_community_detection.py` | Create | Unit tests for all pure functions + orchestration with fakes |
| `tests/test_neo4j_louvain_integration.py` | Create | Real-Neo4j tests for the two new client methods |
| `benchmarks/ibm_aml/patterns.py` | Modify | Generalize parser to non-CYCLE typologies (`load_pattern_groups`) |
| `benchmarks/ibm_aml/louvain_runner.py` | Create | Recall/precision validation against labeled patterns |
| `CLAUDE.md` | Modify | Status, new batch-write pattern, knob docs |

---

### Task 1: Branch reset onto cycle-detection + networkx dependency

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: a working tree containing `fraud/cycle_detector.py`, `db/neo4j.py` (with `FLOWS_TO` writer + `Query` timeout pattern), `db/postgres.py` (with `upsert_risk_flag`), `migrations/002_create_risk_flags_table.sql`, `benchmarks/ibm_aml/`, and importable `networkx`. Every later task assumes this state.

Background: `louvain-clustering` is currently cut from `main` and its only unique commit (`386469e`, a blanket `benchmarks/` gitignore) is superseded by `cycle-detection`'s `.gitignore`, which already ignores `Backend/benchmarks/data/` and `Backend/benchmarks/results/`. A hard reset loses nothing.

- [ ] **Step 1: Verify nothing unique would be lost, then reset**

```bash
cd /Users/kavinnimalarajan/FlowGraph/Backend
git status --short          # must be clean apart from untracked docs/; stop and ask the user if tracked files are dirty
git log --oneline cycle-detection..louvain-clustering   # expect ONLY 386469e "chore: ignore benchmarks directory"
git checkout louvain-clustering    # the session may be sitting on cycle-detection — all work happens on louvain-clustering
git reset --hard cycle-detection
```

Expected: `HEAD is now at 48a0eb2 harden: real-Neo4j integration tests, ...` and `git branch --show-current` prints `louvain-clustering`.

- [ ] **Step 2: Confirm the dependency files arrived**

```bash
ls fraud/cycle_detector.py migrations/002_create_risk_flags_table.sql benchmarks/ibm_aml/runner.py
grep -n "benchmarks" ../.gitignore
```

Expected: all three files exist; `.gitignore` shows `Backend/benchmarks/data/` and `Backend/benchmarks/results/`.

- [ ] **Step 3: Add networkx to requirements.txt**

In `requirements.txt`, after the `redis==5.0.4` line in the "Database drivers" block, add a new block:

```
# Graph algorithms (Louvain community detection)
# networkx 3.2.1 is the last release supporting Python 3.9 — do not bump without a Python upgrade
networkx==3.2.1
```

- [ ] **Step 4: Install and smoke-test the import**

```bash
pip install networkx==3.2.1
python -c "import networkx as nx; g = nx.Graph([(1,2),(2,3),(3,1)]); print(nx.community.louvain_communities(g, seed=42))"
```

Expected: prints `[{1, 2, 3}]` (one community).

- [ ] **Step 5: Run the existing suite to confirm the reset state is green**

```bash
python -m pytest tests/ -m "not integration" -q
```

Expected: PASS (same pass/skip counts as on `cycle-detection`).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt
git commit -m "chore: base louvain work on cycle-detection; add networkx 3.2.1"
```

---

### Task 2: Config knobs + edge weight + undirected graph build

**Files:**
- Modify: `config.py` (append after the `CYCLE_LEVEL_CRITICAL` line)
- Create: `fraud/community_detector.py`
- Create: `tests/test_community_detection.py`

**Interfaces:**
- Consumes: config-knob style from `config.py`'s `CYCLE_*` block.
- Produces:
  - `config.LOUVAIN_WINDOW_DAYS: int`, `LOUVAIN_SEED: int`, `LOUVAIN_RESOLUTION: float`, `LOUVAIN_WEIGHT_MODE: str`, `LOUVAIN_MIN_COMMUNITY_SIZE: int`, `LOUVAIN_CORE_K: int`, `LOUVAIN_EXPORT_TIMEOUT_SECONDS: float`, `LOUVAIN_ASSIGN_BATCH_SIZE: int`, `LOUVAIN_DENSITY_REF: float`, `LOUVAIN_VOLUME_FLOOR_CENTS: int`, `LOUVAIN_VOLUME_CAP_CENTS: int`, `LOUVAIN_OVERLAP_REF: float`, `LOUVAIN_LEVEL_MEDIUM/HIGH/CRITICAL: float`
  - `fraud.community_detector.edge_weight(total_amount_cents: int, tx_count: int, mode: str = LOUVAIN_WEIGHT_MODE) -> float`
  - `fraud.community_detector.build_undirected_graph(edges: List[Dict[str, Any]], weight_mode: str = LOUVAIN_WEIGHT_MODE) -> nx.Graph` where each edge dict is `{"src": str, "dst": str, "total_amount": int, "tx_count": int}` and each resulting graph edge carries `weight: float`, `total_amount: int`, `tx_count: int` attributes.

- [ ] **Step 1: Append the LOUVAIN config block to `config.py`**

After the `CYCLE_LEVEL_CRITICAL` line, append:

```python
# ==================== LOUVAIN COMMUNITY DETECTION ====================
# Daily batch community detection over aggregate FLOWS_TO edges. Runs
# Python-side (networkx louvain_communities by default; optional leidenalg
# engine via LOUVAIN_ENGINE, wired in a later task) — no GDS plugin dependency.
# Communities are scored on five dimensions (size band, density, internal
# volume, isolation/conductance, known-risk overlap); those clearing
# LOUVAIN_LEVEL_MEDIUM persist to risk_flags as flag_type='COMMUNITY'.

LOUVAIN_WINDOW_DAYS = int(os.getenv("LOUVAIN_WINDOW_DAYS", "30"))
# Only FLOWS_TO edges with last_ts inside this window join the graph.
# Communities should reflect *current* money movement. Treated as a tuning
# variable — the IBM AML benchmark measures the runtime/accuracy tradeoff.

LOUVAIN_SEED = int(os.getenv("LOUVAIN_SEED", "42"))
# Louvain is randomized; a fixed seed makes runs reproducible and tests deterministic.

LOUVAIN_RESOLUTION = float(os.getenv("LOUVAIN_RESOLUTION", "1.0"))
# Modularity resolution. >1.0 → more, smaller communities; <1.0 → fewer, larger.

LOUVAIN_WEIGHT_MODE = os.getenv("LOUVAIN_WEIGHT_MODE", "log_amount").lower()
# Edge weight for modularity optimization:
#   "log_amount" — log1p(total_amount): value-aware but whale-dampened, so one
#                  large legitimate payment cannot glue unrelated accounts. Default.
#   "amount"     — raw total_amount cents (pure value; whale-sensitive)
#   "tx_count"   — relationship intensity (repeated transfers), value-blind
#   "unweighted" — every edge weighs 1.0 (pure topology)

LOUVAIN_MIN_COMMUNITY_SIZE = int(os.getenv("LOUVAIN_MIN_COMMUNITY_SIZE", "3"))
# Communities smaller than this are noise: skipped entirely (no node props, no scoring).

LOUVAIN_CORE_K = int(os.getenv("LOUVAIN_CORE_K", "10"))
# Fingerprint = sha256 of the K highest weighted-degree members. The core of a
# ring is stable across daily runs even as the periphery churns, so re-detection
# upserts the same risk_flags row instead of spawning a duplicate alert.
# Documented blindspot: a community whose CORE splits or merges gets a new flag.

LOUVAIN_EXPORT_TIMEOUT_SECONDS = float(os.getenv("LOUVAIN_EXPORT_TIMEOUT_SECONDS", "120.0"))
# Transaction timeout for the FLOWS_TO edge-list export query. A batch job may
# take longer than the per-account cycle budget, but must still be bounded.

LOUVAIN_ASSIGN_BATCH_SIZE = int(os.getenv("LOUVAIN_ASSIGN_BATCH_SIZE", "5000"))
# Rows per UNWIND transaction when writing community_id node properties.

# --- Community scoring knobs ---
LOUVAIN_DENSITY_REF = float(os.getenv("LOUVAIN_DENSITY_REF", "0.15"))
# Internal edge density (2m / n(n-1)) at which density_score saturates to 1.0.

LOUVAIN_VOLUME_FLOOR_CENTS = int(os.getenv("LOUVAIN_VOLUME_FLOOR_CENTS", "1000000"))
# $10k — internal volume at/below this scores ~0.0 for the volume dimension.
LOUVAIN_VOLUME_CAP_CENTS = int(os.getenv("LOUVAIN_VOLUME_CAP_CENTS", "1000000000"))
# $10M — internal volume at/above this scores 1.0 (log scale between floor and cap).

LOUVAIN_OVERLAP_REF = float(os.getenv("LOUVAIN_OVERLAP_REF", "0.25"))
# Fraction of members already flagged by OTHER detectors at which overlap_score
# saturates (0.25 → a quarter of the community already flagged = maximum signal).

# Score thresholds → risk level (lower-bound, inclusive). Communities scoring
# below MEDIUM are NOT persisted to risk_flags (node props are still written).
LOUVAIN_LEVEL_MEDIUM = float(os.getenv("LOUVAIN_LEVEL_MEDIUM", "0.40"))
LOUVAIN_LEVEL_HIGH = float(os.getenv("LOUVAIN_LEVEL_HIGH", "0.65"))
LOUVAIN_LEVEL_CRITICAL = float(os.getenv("LOUVAIN_LEVEL_CRITICAL", "0.85"))
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_community_detection.py`:

```python
"""
Unit tests for the Louvain community detection engine (fraud/community_detector.py).

All tests here are pure — no Neo4j/Postgres. The orchestration tests use fake
clients. Real-Neo4j coverage lives in tests/test_neo4j_louvain_integration.py.
"""

from __future__ import annotations

import math

import pytest

from fraud.community_detector import build_undirected_graph, edge_weight

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# edge_weight
# ---------------------------------------------------------------------------

class TestEdgeWeight:
    def test_log_amount_mode(self):
        assert edge_weight(100, 5, mode="log_amount") == pytest.approx(math.log1p(100))

    def test_amount_mode(self):
        assert edge_weight(2500, 5, mode="amount") == 2500.0

    def test_tx_count_mode(self):
        assert edge_weight(2500, 5, mode="tx_count") == 5.0

    def test_unweighted_mode(self):
        assert edge_weight(2500, 5, mode="unweighted") == 1.0

    def test_negative_amount_clamped_to_zero_weight(self):
        assert edge_weight(-100, 1, mode="log_amount") == 0.0

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            edge_weight(100, 1, mode="bogus")


# ---------------------------------------------------------------------------
# build_undirected_graph
# ---------------------------------------------------------------------------

class TestBuildUndirectedGraph:
    def test_opposite_directions_collapse_to_one_edge(self):
        edges = [
            {"src": "A", "dst": "B", "total_amount": 100, "tx_count": 2},
            {"src": "B", "dst": "A", "total_amount": 50, "tx_count": 1},
        ]
        g = build_undirected_graph(edges, weight_mode="log_amount")
        assert g.number_of_edges() == 1
        attrs = g["A"]["B"]
        # Raw aggregates sum across both directions
        assert attrs["total_amount"] == 150
        assert attrs["tx_count"] == 3
        # Weights are computed per directed record, then summed
        assert attrs["weight"] == pytest.approx(math.log1p(100) + math.log1p(50))

    def test_self_loops_dropped(self):
        edges = [{"src": "A", "dst": "A", "total_amount": 100, "tx_count": 1}]
        g = build_undirected_graph(edges)
        assert g.number_of_nodes() == 0
        assert g.number_of_edges() == 0

    def test_empty_input_gives_empty_graph(self):
        g = build_undirected_graph([])
        assert g.number_of_nodes() == 0

    def test_distinct_pairs_stay_distinct(self):
        edges = [
            {"src": "A", "dst": "B", "total_amount": 100, "tx_count": 1},
            {"src": "B", "dst": "C", "total_amount": 100, "tx_count": 1},
        ]
        g = build_undirected_graph(edges)
        assert g.number_of_edges() == 2
        assert set(g.nodes) == {"A", "B", "C"}
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fraud.community_detector'`.

- [ ] **Step 4: Write the implementation**

Create `fraud/community_detector.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add config.py fraud/community_detector.py tests/test_community_detection.py
git commit -m "feat: louvain config knobs + weighted undirected graph build"
```

---

### Task 3: Core-member selection + community fingerprint

**Files:**
- Modify: `fraud/community_detector.py`
- Modify: `tests/test_community_detection.py`

**Interfaces:**
- Consumes: `build_undirected_graph`'s `nx.Graph` (edges carry `weight`).
- Produces:
  - `core_members(graph: nx.Graph, members: Iterable[str], k: int = LOUVAIN_CORE_K) -> List[str]` — sorted list of ≤ k member ids.
  - `community_fingerprint(core: Iterable[str]) -> str` — 64-char hex sha256. Raises `ValueError` on empty input.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_community_detection.py` (extend the import line to `from fraud.community_detector import build_undirected_graph, community_fingerprint, core_members, edge_weight`):

```python
# ---------------------------------------------------------------------------
# core_members + community_fingerprint
# ---------------------------------------------------------------------------

def _weighted_graph():
    """A=15, B=11, C=7, D=1 weighted degree."""
    edges = [
        {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 10},
        {"src": "A", "dst": "C", "total_amount": 0, "tx_count": 5},
        {"src": "B", "dst": "C", "total_amount": 0, "tx_count": 1},
        {"src": "C", "dst": "D", "total_amount": 0, "tx_count": 1},
    ]
    return build_undirected_graph(edges, weight_mode="tx_count")


class TestCoreMembers:
    def test_picks_top_k_by_weighted_degree(self):
        g = _weighted_graph()
        assert core_members(g, ["A", "B", "C", "D"], k=2) == ["A", "B"]

    def test_k_larger_than_community_returns_all_sorted(self):
        g = _weighted_graph()
        assert core_members(g, ["C", "D"], k=10) == ["C", "D"]

    def test_degree_computed_within_subgraph_only(self):
        # Restricted to {B, C, D}: B–C (1) and C–D (1) → C has degree 2, B and D have 1.
        g = _weighted_graph()
        assert core_members(g, ["B", "C", "D"], k=1) == ["C"]

    def test_ties_break_lexicographically(self):
        edges = [
            {"src": "X", "dst": "Y", "total_amount": 0, "tx_count": 1},
            {"src": "Y", "dst": "Z", "total_amount": 0, "tx_count": 1},
        ]
        g = build_undirected_graph(edges, weight_mode="tx_count")
        # X and Z tie at weighted degree 1 → X wins lexicographically
        assert core_members(g, ["X", "Y", "Z"], k=2) == ["X", "Y"]


class TestCommunityFingerprint:
    def test_order_invariant(self):
        assert community_fingerprint(["b", "a", "c"]) == community_fingerprint(["c", "a", "b"])

    def test_different_core_different_fingerprint(self):
        assert community_fingerprint(["a", "b"]) != community_fingerprint(["a", "c"])

    def test_is_64_hex_chars(self):
        fp = community_fingerprint(["a"])
        assert len(fp) == 64
        int(fp, 16)  # raises if not hex

    def test_empty_core_raises(self):
        with pytest.raises(ValueError):
            community_fingerprint([])
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: FAIL with `ImportError: cannot import name 'community_fingerprint'`.

- [ ] **Step 3: Implement**

Append to `fraud/community_detector.py` after `build_undirected_graph`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add fraud/community_detector.py tests/test_community_detection.py
git commit -m "feat: community core selection + stable fingerprint"
```

---

### Task 4: 5-dimension community scorer

**Files:**
- Modify: `fraud/community_detector.py`
- Modify: `tests/test_community_detection.py`

**Interfaces:**
- Consumes: config knobs from Task 2.
- Produces:

```python
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
) -> Dict[str, Any]
# returns {"risk_score": float, "risk_level": str, "explanation": str, "details": dict}
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_community_detection.py` (add `score_community` to the import):

```python
# ---------------------------------------------------------------------------
# score_community
# ---------------------------------------------------------------------------

class TestScoreCommunity:
    def test_dense_ring_sized_high_volume_flagged_community_is_critical(self):
        # 8 members, complete graph (28 edges, density 1.0), $1.4M internal,
        # 2 members already flagged by other detectors (25% → overlap saturates).
        members = [f"S{i}" for i in range(8)]
        result = score_community(
            member_ids=members,
            internal_edge_count=28,
            internal_total_cents=140_000_000,
            flagged_member_count=2,
        )
        assert result["risk_level"] == "critical"
        assert result["risk_score"] >= 0.85

    def test_small_low_volume_community_is_low(self):
        # 3-node chain, $400 total, nobody flagged.
        result = score_community(
            member_ids=["B0", "B1", "B2"],
            internal_edge_count=2,
            internal_total_cents=40_000,
            flagged_member_count=0,
        )
        assert result["risk_level"] == "low"
        assert result["risk_score"] < 0.40

    def test_overlap_raises_score_monotonically(self):
        members = [f"M{i}" for i in range(10)]
        base = dict(member_ids=members, internal_edge_count=12,
                    internal_total_cents=50_000_000)
        s0 = score_community(flagged_member_count=0, **base)["risk_score"]
        s2 = score_community(flagged_member_count=2, **base)["risk_score"]
        s5 = score_community(flagged_member_count=5, **base)["risk_score"]
        assert s0 < s2 <= s5

    def test_huge_community_scores_low_on_size(self):
        members = [f"H{i}" for i in range(500)]
        result = score_community(
            member_ids=members,
            internal_edge_count=600,
            internal_total_cents=500_000_000,
            flagged_member_count=0,
        )
        assert result["details"]["size_score"] == pytest.approx(0.1)

    def test_explanation_always_nonempty_and_mentions_key_facts(self):
        result = score_community(
            member_ids=["A", "B", "C", "D", "E"],
            internal_edge_count=6,
            internal_total_cents=25_000_000,
            flagged_member_count=1,
        )
        text = result["explanation"]
        assert text
        assert "5 accounts" in text
        assert result["risk_level"] in text

    def test_details_carry_all_five_dimension_scores(self):
        result = score_community(
            member_ids=["A", "B", "C", "D"],
            internal_edge_count=4,
            internal_total_cents=10_000_000,
            flagged_member_count=0,
            conductance=0.3,
        )
        for key in ("size_score", "density_score", "volume_score",
                    "overlap_score", "cohesion_score"):
            assert 0.0 <= result["details"][key] <= 1.0
        assert result["details"]["conductance"] == pytest.approx(0.3)

    def test_higher_conductance_lowers_score(self):
        # A leaky community (much flow crosses the boundary) is less suspicious
        # than an otherwise-identical isolated one.
        base = dict(
            member_ids=[f"M{i}" for i in range(6)],
            internal_edge_count=10,
            internal_total_cents=50_000_000,
            flagged_member_count=1,
        )
        isolated = score_community(conductance=0.0, **base)["risk_score"]
        leaky = score_community(conductance=1.0, **base)["risk_score"]
        assert leaky < isolated

    def test_fewer_than_two_members_raises(self):
        with pytest.raises(ValueError):
            score_community(
                member_ids=["A"],
                internal_edge_count=0,
                internal_total_cents=0,
                flagged_member_count=0,
            )
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: FAIL with `ImportError: cannot import name 'score_community'`.

- [ ] **Step 3: Implement**

Append to `fraud/community_detector.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add fraud/community_detector.py tests/test_community_detection.py
git commit -m "feat: 5-dimension community risk scorer with written explanations"
```

---

### Task 5: Postgres — flagged-account lookup

**Files:**
- Modify: `db/postgres.py` (add method after `get_risk_flags`)
- Modify: `tests/test_community_detection.py`

**Interfaces:**
- Consumes: `PostgresClient._get_connection()` context manager and the `risk_flags` table (migration 002).
- Produces: `PostgresClient.get_flagged_account_ids(status: str = "open", exclude_flag_type: Optional[str] = None) -> List[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_community_detection.py`:

```python
# ---------------------------------------------------------------------------
# PostgresClient.get_flagged_account_ids (query construction — connection faked)
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows


class _FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_get_flagged_account_ids_excludes_flag_type(monkeypatch):
    from db.postgres import PostgresClient

    client = PostgresClient()
    conn = _FakeConn(rows=[{"account_id": "ACC1"}, {"account_id": "ACC2"}])
    monkeypatch.setattr(client, "_get_connection", lambda: _FakeAcquire(conn))

    ids = await client.get_flagged_account_ids(status="open", exclude_flag_type="COMMUNITY")

    assert ids == ["ACC1", "ACC2"]
    query, args = conn.calls[0]
    assert "flag_type <>" in query
    assert args == ("open", "COMMUNITY")


@pytest.mark.asyncio
async def test_get_flagged_account_ids_without_exclusion(monkeypatch):
    from db.postgres import PostgresClient

    client = PostgresClient()
    conn = _FakeConn(rows=[])
    monkeypatch.setattr(client, "_get_connection", lambda: _FakeAcquire(conn))

    ids = await client.get_flagged_account_ids()

    assert ids == []
    query, args = conn.calls[0]
    assert "flag_type" not in query
    assert args == ("open",)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_community_detection.py -k flagged_account -q
```

Expected: FAIL with `AttributeError: 'PostgresClient' object has no attribute 'get_flagged_account_ids'`.

- [ ] **Step 3: Implement**

In `db/postgres.py`, add after the `get_risk_flags` method (inside `PostgresClient`):

```python
    async def get_flagged_account_ids(
        self,
        status: str = "open",
        exclude_flag_type: Optional[str] = None,
    ) -> List[str]:
        """
        Distinct account IDs appearing in risk_flags with the given status.

        exclude_flag_type exists so a detector can measure corroboration from
        OTHER detectors without feeding on its own output: the community scorer
        passes exclude_flag_type='COMMUNITY', otherwise yesterday's community
        flag would inflate today's overlap score in a feedback loop.

        Args:
            status:            Flag status to include ('open' by default)
            exclude_flag_type: Skip flags of this detector type, or None for all

        Returns:
            Sorted list of distinct account IDs
        """
        if exclude_flag_type:
            query = """
            SELECT DISTINCT unnest(account_ids) AS account_id
            FROM risk_flags
            WHERE status = $1 AND flag_type <> $2
            ORDER BY account_id
            """
            args = (status, exclude_flag_type)
        else:
            query = """
            SELECT DISTINCT unnest(account_ids) AS account_id
            FROM risk_flags
            WHERE status = $1
            ORDER BY account_id
            """
            args = (status,)

        async with self._get_connection() as conn:
            rows = await conn.fetch(query, *args)
        return [row["account_id"] for row in rows]
```

- [ ] **Step 4: Run to verify pass**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: 28 passed.

- [ ] **Step 5: Commit**

```bash
git add db/postgres.py tests/test_community_detection.py
git commit -m "feat: postgres lookup of accounts flagged by other detectors"
```

---

### Task 6: Neo4j — edge export + community assignment writes

**Files:**
- Modify: `db/neo4j.py` (two methods after `find_cycles`; extend the `config` import)
- Create: `tests/test_neo4j_louvain_integration.py`

**Interfaces:**
- Consumes: `Neo4jClient.driver`, `NEO4J_DATABASE`, the `Query` timeout pattern from `find_cycles`, `upsert_transaction_graph` (integration-test seeding), config knobs from Task 2.
- Produces:
  - `Neo4jClient.export_flows_to_edges(window_days: int = LOUVAIN_WINDOW_DAYS, reference_time: Optional[datetime] = None, query_timeout_seconds: float = LOUVAIN_EXPORT_TIMEOUT_SECONDS) -> List[Dict[str, Any]]` — dicts of `{src, dst, total_amount, tx_count}`.
  - `Neo4jClient.write_community_assignments(assignments: Dict[str, str], detected_at_epoch: int, batch_size: int = LOUVAIN_ASSIGN_BATCH_SIZE) -> int` — returns number of rows written.

These are exercised against a real Neo4j (mocking the async driver validates nothing about the Cypher). The test module follows `tests/test_neo4j_cycle_integration.py` exactly: `ITEST_LV_` id prefix, wipe before/after, module skips when Neo4j is unreachable. **This task needs `docker compose up neo4j` running.**

- [ ] **Step 1: Write the failing integration tests**

Create `tests/test_neo4j_louvain_integration.py`:

```python
"""
Real-Neo4j integration tests for the Louvain data path:
export_flows_to_edges (window filtering, aggregate fields) and
write_community_assignments (batched node-property writes).

Connects to the Neo4j configured via NEO4J_URI / NEO4J_PASSWORD (the
docker-compose instance). If no Neo4j is reachable, the module is skipped.
Isolation: every account id is prefixed ITEST_LV_ and wiped before/after.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from db.neo4j import Neo4jClient, NEO4J_DATABASE

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PREFIX = "ITEST_LV_"
_NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


async def _reachable(client: Neo4jClient) -> bool:
    try:
        await client.initialize()
        async with client.driver.session(database=NEO4J_DATABASE) as s:
            await s.run("RETURN 1")
        return True
    except Exception:
        return False


async def _wipe(client: Neo4jClient) -> None:
    async with client.driver.session(database=NEO4J_DATABASE) as s:
        await s.run(
            "MATCH (a:Account) WHERE a.id STARTS WITH $p DETACH DELETE a",
            p=_PREFIX,
        )


@pytest_asyncio.fixture
async def neo4j():
    client = Neo4jClient()
    if not await _reachable(client):
        await client.close()
        pytest.skip("No Neo4j reachable at NEO4J_URI — start `docker compose up neo4j`")
    await client.init_constraints()
    await _wipe(client)
    yield client
    await _wipe(client)
    await client.close()


async def _seed_edge(client, src, dst, amount_cents, ts, txn_id):
    await client.upsert_transaction_graph(
        sender_id=f"{_PREFIX}{src}",
        receiver_id=f"{_PREFIX}{dst}",
        amount_cents=amount_cents,
        timestamp_utc=ts,
        rail="WIRE",
        event_type="SETTLEMENT",
        transaction_id=f"{_PREFIX}{txn_id}",
        idempotency_key=f"{_PREFIX}{txn_id}",
    )


def _itest_only(edges):
    """The shared docker Neo4j may hold benchmark data — filter to our namespace."""
    return [e for e in edges if e["src"].startswith(_PREFIX) and e["dst"].startswith(_PREFIX)]


class TestExportFlowsToEdges:
    async def test_returns_aggregates_and_respects_window(self, neo4j):
        # Two txns A→B inside the window (aggregate: 30_000 cents, tx_count 2),
        # one txn C→D far outside it.
        await _seed_edge(neo4j, "A", "B", 10_000, _NOW - timedelta(days=1), "ab1")
        await _seed_edge(neo4j, "A", "B", 20_000, _NOW - timedelta(days=2), "ab2")
        await _seed_edge(neo4j, "C", "D", 50_000, _NOW - timedelta(days=90), "cd1")

        edges = _itest_only(
            await neo4j.export_flows_to_edges(window_days=30, reference_time=_NOW)
        )

        assert len(edges) == 1
        edge = edges[0]
        assert edge["src"] == f"{_PREFIX}A"
        assert edge["dst"] == f"{_PREFIX}B"
        assert edge["total_amount"] == 30_000
        assert edge["tx_count"] == 2

    async def test_stale_edge_included_when_window_covers_it(self, neo4j):
        await _seed_edge(neo4j, "C", "D", 50_000, _NOW - timedelta(days=90), "cd1")

        edges = _itest_only(
            await neo4j.export_flows_to_edges(window_days=120, reference_time=_NOW)
        )

        assert len(edges) == 1
        assert edges[0]["src"] == f"{_PREFIX}C"


class TestWriteCommunityAssignments:
    async def test_writes_props_and_returns_count(self, neo4j):
        await _seed_edge(neo4j, "A", "B", 10_000, _NOW - timedelta(days=1), "ab1")

        detected_at = int(_NOW.timestamp())
        written = await neo4j.write_community_assignments(
            {f"{_PREFIX}A": "abc123def456", f"{_PREFIX}B": "abc123def456"},
            detected_at_epoch=detected_at,
        )

        assert written == 2
        async with neo4j.driver.session(database=NEO4J_DATABASE) as s:
            result = await s.run(
                "MATCH (a:Account) WHERE a.id STARTS WITH $p "
                "RETURN a.id AS id, a.community_id AS cid, "
                "a.community_detected_at AS ts ORDER BY id",
                p=_PREFIX,
            )
            records = [r async for r in result]

        assert [r["cid"] for r in records] == ["abc123def456", "abc123def456"]
        assert all(r["ts"] == detected_at for r in records)

    async def test_empty_assignments_writes_nothing(self, neo4j):
        written = await neo4j.write_community_assignments({}, detected_at_epoch=0)
        assert written == 0

    async def test_batching_splits_large_maps(self, neo4j):
        # Seed 7 accounts, write with batch_size=3 → 3 transactions, all rows land.
        for i in range(7):
            await _seed_edge(neo4j, f"N{i}", f"N{(i + 1) % 7}", 10_000,
                             _NOW - timedelta(days=1), f"n{i}")

        assignments = {f"{_PREFIX}N{i}": "fff000fff000" for i in range(7)}
        written = await neo4j.write_community_assignments(
            assignments, detected_at_epoch=int(_NOW.timestamp()), batch_size=3
        )

        assert written == 7
        async with neo4j.driver.session(database=NEO4J_DATABASE) as s:
            result = await s.run(
                "MATCH (a:Account) WHERE a.id STARTS WITH $p "
                "AND a.community_id = 'fff000fff000' RETURN count(a) AS n",
                p=_PREFIX,
            )
            record = await result.single()
        assert record["n"] == 7
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose -f ../docker-compose.yml up -d neo4j   # if not already running
python -m pytest tests/test_neo4j_louvain_integration.py -q
```

Expected: FAIL with `AttributeError: 'Neo4jClient' object has no attribute 'export_flows_to_edges'` (or SKIP if Neo4j is down — start it, these tests must actually run).

- [ ] **Step 3: Implement the two client methods**

In `db/neo4j.py`, extend the existing `from config import (...)` block with:

```python
    LOUVAIN_WINDOW_DAYS,
    LOUVAIN_EXPORT_TIMEOUT_SECONDS,
    LOUVAIN_ASSIGN_BATCH_SIZE,
```

Then add after the `find_cycles` method (inside `Neo4jClient`):

```python
    async def export_flows_to_edges(
        self,
        window_days: int = LOUVAIN_WINDOW_DAYS,
        reference_time: Optional[datetime] = None,
        query_timeout_seconds: float = LOUVAIN_EXPORT_TIMEOUT_SECONDS,
    ) -> List[Dict[str, Any]]:
        """
        Export the aggregate FLOWS_TO edge list for community detection.

        Returns one record per directed account pair whose relationship was
        active (last_ts) within the window. The Louvain batch collapses the
        two directions Python-side (build_undirected_graph); exporting raw
        directed records keeps this query a trivial scan.

        Args:
            window_days:    Only edges with last_ts within this many days
            reference_time: Window anchor; defaults to now (benchmarks anchor
                            to the dataset's own max timestamp instead)

        Returns:
            List of dicts: {src, dst, total_amount, tx_count}
        """
        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)
        window_start_epoch = int(ref.timestamp()) - window_days * 86400

        query = """
        MATCH (a:Account)-[f:FLOWS_TO]->(b:Account)
        WHERE f.last_ts >= $window_start_epoch
        RETURN a.id AS src, b.id AS dst,
               f.total_amount AS total_amount, f.tx_count AS tx_count
        """
        # Batch export may legitimately take longer than the per-account cycle
        # budget, but must still be bounded — an unindexed runaway scan cannot
        # be allowed to hang the batch forever.
        timed_query = Query(query, timeout=query_timeout_seconds)

        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                result = await session.run(
                    timed_query, window_start_epoch=window_start_epoch
                )
                return [
                    {
                        "src":          record["src"],
                        "dst":          record["dst"],
                        "total_amount": record["total_amount"],
                        "tx_count":     record["tx_count"],
                    }
                    async for record in result
                ]
        except Exception as e:
            logger.error(f"Failed to export FLOWS_TO edges: {e}")
            raise

    async def write_community_assignments(
        self,
        assignments: Dict[str, str],
        detected_at_epoch: int,
        batch_size: int = LOUVAIN_ASSIGN_BATCH_SIZE,
    ) -> int:
        """
        Batch-write community membership onto Account nodes.

        Sets community_id / community_detected_at as node properties. This is
        derived analytical state, NOT payment data — the outbox convention
        (Postgres first, then graph) applies to payment events; batch algorithm
        results are written directly, with detected_at recording provenance.
        MATCH (not MERGE) is deliberate: an account absent from the graph was
        deleted since export, and resurrecting it here would be wrong.

        Args:
            assignments:       account_id → community_id (12-hex-char string)
            detected_at_epoch: Run anchor time, unix seconds UTC

        Returns:
            Number of assignment rows written
        """
        if not assignments:
            return 0

        rows = [{"id": account_id, "cid": community_id}
                for account_id, community_id in assignments.items()]

        query = """
        UNWIND $rows AS row
        MATCH (a:Account {id: row.id})
        SET a.community_id = row.cid,
            a.community_detected_at = $detected_at
        """

        written = 0
        try:
            async with self.driver.session(database=NEO4J_DATABASE) as session:
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    result = await session.run(
                        query, rows=batch, detected_at=detected_at_epoch
                    )
                    await result.consume()
                    written += len(batch)
            logger.info(
                "Community assignments written | accounts=%d communities=%d",
                written, len(set(assignments.values())),
            )
            return written
        except Exception as e:
            logger.error(f"Failed to write community assignments: {e}")
            raise
```

- [ ] **Step 4: Run to verify pass**

```bash
python -m pytest tests/test_neo4j_louvain_integration.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Run the full non-integration suite to check for regressions**

```bash
python -m pytest tests/ -m "not integration" -q
```

Expected: PASS, no new failures.

- [ ] **Step 6: Commit**

```bash
git add db/neo4j.py tests/test_neo4j_louvain_integration.py
git commit -m "feat: neo4j FLOWS_TO edge export + batched community assignment writes"
```

---

### Task 7: CommunityDetector orchestration

**Files:**
- Modify: `fraud/community_detector.py`
- Modify: `tests/test_community_detection.py`

**Interfaces:**
- Consumes: everything produced by Tasks 2–6: `build_undirected_graph`, `core_members`, `community_fingerprint`, `score_community`, `Neo4jClient.export_flows_to_edges` / `write_community_assignments`, `PostgresClient.get_flagged_account_ids` / `upsert_risk_flag`.
- Produces:

```python
def community_conductance(graph: nx.Graph, members: Iterable[str]) -> float
def split_disconnected(communities: Iterable[Iterable[str]], graph: nx.Graph) -> List[Set[str]]

class CommunityDetector:
    def __init__(self, neo4j_client: Any, postgres_client: Any) -> None: ...
    async def run(self, reference_time: "Optional[datetime]" = None) -> Dict[str, Any]
    # returns {"communities": int, "assignments": int, "flags": List[Dict]}
    # each flag: {"fingerprint", "community_id", "account_ids", "risk_score",
    #             "risk_level", "explanation", "details"}
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_community_detection.py` (extend the import to `from fraud.community_detector import CommunityDetector, build_undirected_graph, community_conductance, community_fingerprint, core_members, edge_weight, score_community, split_disconnected`):

```python
# ---------------------------------------------------------------------------
# split_disconnected + community_conductance (pure helpers)
# ---------------------------------------------------------------------------

class TestSplitDisconnected:
    def test_connected_community_passes_through(self):
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
            {"src": "B", "dst": "C", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        assert split_disconnected([{"A", "B", "C"}], g) == [{"A", "B", "C"}]

    def test_disconnected_community_is_split(self):
        # One 'community' spanning two disjoint edges must break into two pieces.
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
            {"src": "C", "dst": "D", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        out = split_disconnected([{"A", "B", "C", "D"}], g)
        assert {frozenset(s) for s in out} == {frozenset({"A", "B"}), frozenset({"C", "D"})}


class TestCommunityConductance:
    def test_isolated_component_has_zero_conductance(self):
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
            {"src": "C", "dst": "D", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        assert community_conductance(g, {"A", "B"}) == 0.0

    def test_whole_graph_conductance_is_zero_not_error(self):
        # S == all nodes → empty complement → nx.conductance divides by zero;
        # the helper must guard and return 0.0, not raise.
        g = build_undirected_graph([
            {"src": "A", "dst": "B", "total_amount": 0, "tx_count": 1},
        ], weight_mode="tx_count")
        assert community_conductance(g, {"A", "B"}) == 0.0


# ---------------------------------------------------------------------------
# CommunityDetector orchestration (fake clients)
# ---------------------------------------------------------------------------

class FakeNeo4j:
    def __init__(self, edges):
        self.edges = edges
        self.assignments = None
        self.detected_at = None

    async def export_flows_to_edges(self, **kwargs):
        return self.edges

    async def write_community_assignments(self, assignments, detected_at_epoch, **kwargs):
        self.assignments = assignments
        self.detected_at = detected_at_epoch
        return len(assignments)


class FakePostgres:
    def __init__(self, flagged=()):
        self.flagged = list(flagged)
        self.upserts = []
        self.lookup_kwargs = None

    async def get_flagged_account_ids(self, **kwargs):
        self.lookup_kwargs = kwargs
        return self.flagged

    async def upsert_risk_flag(self, **kwargs):
        self.upserts.append(kwargs)


def _two_community_edges():
    """
    Two disconnected clusters (Louvain must separate disconnected components):
      - suspicious: 8 accounts S0..S7, complete graph, $50k per corridor,
        S0 and S1 already flagged by the cycle detector
      - benign: B0—B1—B2 chain, $200 per corridor
    """
    edges = []
    suspicious = [f"S{i}" for i in range(8)]
    for i in range(8):
        for j in range(i + 1, 8):
            edges.append({
                "src": suspicious[i], "dst": suspicious[j],
                "total_amount": 5_000_000, "tx_count": 3,
            })
    edges.append({"src": "B0", "dst": "B1", "total_amount": 20_000, "tx_count": 1})
    edges.append({"src": "B1", "dst": "B2", "total_amount": 20_000, "tx_count": 1})
    return edges


@pytest.mark.asyncio
async def test_detector_flags_suspicious_community_only():
    neo4j = FakeNeo4j(_two_community_edges())
    postgres = FakePostgres(flagged=["S0", "S1"])
    detector = CommunityDetector(neo4j, postgres)

    result = await detector.run()

    # Both communities kept (benign trio meets MIN_COMMUNITY_SIZE=3) …
    assert result["communities"] == 2
    # … all 11 accounts got node-property assignments …
    assert result["assignments"] == 11
    assert set(neo4j.assignments.keys()) == {f"S{i}" for i in range(8)} | {"B0", "B1", "B2"}
    # … but only the dense high-volume corroborated cluster was flagged.
    assert len(postgres.upserts) == 1
    flag = postgres.upserts[0]
    assert flag["flag_type"] == "COMMUNITY"
    assert sorted(flag["account_ids"]) == [f"S{i}" for i in range(8)]
    assert flag["risk_level"] == "critical"
    assert flag["explanation"]
    assert flag["details"]["community_id"] == flag["fingerprint"][:12]
    assert flag["details"]["core_members"]


@pytest.mark.asyncio
async def test_detector_excludes_own_flag_type_from_overlap():
    neo4j = FakeNeo4j(_two_community_edges())
    postgres = FakePostgres()
    detector = CommunityDetector(neo4j, postgres)

    await detector.run()

    assert postgres.lookup_kwargs == {"status": "open", "exclude_flag_type": "COMMUNITY"}


@pytest.mark.asyncio
async def test_detector_assigns_members_of_same_community_same_id():
    neo4j = FakeNeo4j(_two_community_edges())
    detector = CommunityDetector(neo4j, FakePostgres())

    await detector.run()

    s_ids = {neo4j.assignments[f"S{i}"] for i in range(8)}
    b_ids = {neo4j.assignments[b] for b in ("B0", "B1", "B2")}
    assert len(s_ids) == 1
    assert len(b_ids) == 1
    assert s_ids != b_ids


@pytest.mark.asyncio
async def test_detector_skips_undersized_communities():
    edges = [{"src": "X", "dst": "Y", "total_amount": 5_000_000, "tx_count": 2}]
    neo4j = FakeNeo4j(edges)
    detector = CommunityDetector(neo4j, FakePostgres())

    result = await detector.run()

    assert result["communities"] == 0
    assert result["assignments"] == 0
    assert neo4j.assignments == {}


@pytest.mark.asyncio
async def test_detector_empty_graph_is_a_noop():
    neo4j = FakeNeo4j([])
    postgres = FakePostgres()
    detector = CommunityDetector(neo4j, postgres)

    result = await detector.run()

    assert result == {"communities": 0, "assignments": 0, "flags": []}
    assert postgres.upserts == []


@pytest.mark.asyncio
async def test_detector_without_postgres_still_returns_flags():
    neo4j = FakeNeo4j(_two_community_edges())
    detector = CommunityDetector(neo4j, postgres_client=None)

    result = await detector.run()

    # No overlap signal (max composite 0.65) but volume+density+size still
    # clear the medium bar; flags are computed and returned, just not persisted.
    assert len(result["flags"]) == 1
    assert result["flags"][0]["risk_level"] in ("medium", "high")
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: FAIL with `ImportError: cannot import name 'community_conductance'`.

- [ ] **Step 3: Implement**

In `fraud/community_detector.py`, extend the module imports:

```python
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set
```

Append after `score_community`, the two pure graph helpers:

```python
# ---------------------------------------------------------------------------
# Connectivity + conductance helpers (pure — operate on the built graph)
# ---------------------------------------------------------------------------

def community_conductance(graph: nx.Graph, members: Iterable[str]) -> float:
    """
    Fraction of a community's edge weight that crosses its boundary.

    Low = an isolated cluster money stays inside (suspicious); high = a dense
    sub-region of otherwise-normal traffic. Returns 0.0 for a community that
    spans the whole graph — there is no boundary, and nx.conductance would
    divide by zero (empty complement).
    """
    member_set = set(members)
    if len(member_set) >= graph.number_of_nodes():
        return 0.0
    try:
        return nx.conductance(graph, member_set, weight="weight")
    except ZeroDivisionError:
        return 0.0


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
    ) -> Dict[str, Any]:
        """
        One full batch pass.

        Steps:
        1. Export FLOWS_TO edges active within LOUVAIN_WINDOW_DAYS
        2. Build the undirected weighted graph, run seeded Louvain
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

        Returns:
            {"communities": kept count, "assignments": node props written,
             "flags": list of flag dicts (persisted ones when postgres present)}
        """
        ref = reference_time if reference_time is not None else datetime.now(timezone.utc)

        edges = await self.neo4j.export_flows_to_edges(
            window_days=LOUVAIN_WINDOW_DAYS, reference_time=ref
        )
        graph = build_undirected_graph(edges)
        if graph.number_of_nodes() == 0:
            logger.info("Louvain batch: no active FLOWS_TO edges in window — nothing to do")
            return {"communities": 0, "assignments": 0, "flags": []}

        raw_communities = nx.community.louvain_communities(
            graph,
            weight="weight",
            resolution=LOUVAIN_RESOLUTION,
            seed=LOUVAIN_SEED,
        )
        # Guarantee each community is internally connected before it earns an
        # identity/fingerprint (Louvain can emit disconnected communities).
        communities = split_disconnected(raw_communities, graph)

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
                conductance=community_conductance(graph, members),
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
```

- [ ] **Step 4: Run to verify pass**

```bash
python -m pytest tests/test_community_detection.py -q
```

Expected: 38 passed.

- [ ] **Step 5: Commit**

```bash
git add fraud/community_detector.py tests/test_community_detection.py
git commit -m "feat: CommunityDetector — connectivity split + 5D scoring orchestration"
```

---

### Task 8: Pluggable Leiden engine (`LOUVAIN_ENGINE`)

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py` (append after `LOUVAIN_LEVEL_CRITICAL`)
- Modify: `fraud/community_detector.py` (add `partition_graph` + `_leiden_partition`; extend config import; refactor `run()`'s inline Louvain call)
- Modify: `tests/test_community_detection.py`

**Interfaces:**
- Consumes: `build_undirected_graph`'s `nx.Graph`; `config.LOUVAIN_ENGINE` (new).
- Produces: `partition_graph(graph: nx.Graph, engine: str = LOUVAIN_ENGINE, resolution: float = LOUVAIN_RESOLUTION, seed: int = LOUVAIN_SEED) -> List[Set[str]]`. `CommunityDetector.run()` calls it instead of `nx.community.louvain_communities` directly, so the engine is swappable by env var with zero call-site change downstream.

**Why this is scalable (the concern that motivated adding it):** Both engines pull the whole windowed edge list into Python memory — that Neo4j export is the shared ceiling, identical for either engine. The Leiden path's only extra cost is a single O(E) `networkx → igraph` conversion (C-backed, milliseconds), dwarfed by the clustering itself. Leiden's C/C++ core is *faster* than networkx's pure-Python Louvain and uses less memory, so switching engines **raises** the practical ceiling. When the whole-graph-in-memory limit is itself the problem, the escape hatch is server-side GDS `gds.leiden` (documented follow-up), which the conversion has no bearing on.

- [ ] **Step 1: Add the optional Leiden dependencies**

In `requirements.txt`, after the `networkx==3.2.1` block, add:

```
# Optional Leiden community-detection engine (LOUVAIN_ENGINE=leiden).
# PyPI package is `igraph` (NOT the deprecated `python-igraph`). Both are
# GPL-3.0 — the default engine stays networkx so this stays opt-in.
igraph==1.0.0
leidenalg==0.12.0
```

- [ ] **Step 2: Add the `LOUVAIN_ENGINE` knob to `config.py`**

After the `LOUVAIN_LEVEL_CRITICAL` line, append:

```python
LOUVAIN_ENGINE = os.getenv("LOUVAIN_ENGINE", "networkx").lower()
# Community-detection engine:
#   "networkx" — pure-Python networkx Louvain, zero extra dependencies. Default.
#   "leiden"   — leidenalg/igraph Leiden: C/C++ core (faster on large graphs),
#                communities internally connected by construction. Requires the
#                optional igraph + leidenalg (GPL) packages to be installed.
```

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_community_detection.py` (add `partition_graph` to the `fraud.community_detector` import):

```python
# ---------------------------------------------------------------------------
# partition_graph engine dispatch
# ---------------------------------------------------------------------------

def _two_triangles():
    """Two disconnected triangles — any correct engine returns exactly these two."""
    edges = []
    for a, b, c in (("A", "B", "C"), ("X", "Y", "Z")):
        edges += [
            {"src": a, "dst": b, "total_amount": 0, "tx_count": 5},
            {"src": b, "dst": c, "total_amount": 0, "tx_count": 5},
            {"src": a, "dst": c, "total_amount": 0, "tx_count": 5},
        ]
    return build_undirected_graph(edges, weight_mode="tx_count")


class TestPartitionGraph:
    def test_networkx_engine_finds_both_clusters(self):
        parts = partition_graph(_two_triangles(), engine="networkx")
        assert {frozenset(p) for p in parts} == {
            frozenset({"A", "B", "C"}), frozenset({"X", "Y", "Z"})
        }

    def test_leiden_engine_finds_both_clusters(self):
        pytest.importorskip("leidenalg")
        pytest.importorskip("igraph")
        parts = partition_graph(_two_triangles(), engine="leiden")
        assert {frozenset(p) for p in parts} == {
            frozenset({"A", "B", "C"}), frozenset({"X", "Y", "Z"})
        }
        assert sum(len(p) for p in parts) == 6  # every node placed exactly once

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError):
            partition_graph(_two_triangles(), engine="bogus")
```

- [ ] **Step 4: Run to verify failure**

```bash
python -m pytest tests/test_community_detection.py -k partition_graph -q
```

Expected: FAIL with `ImportError: cannot import name 'partition_graph'`.

- [ ] **Step 5: Implement `partition_graph` + `_leiden_partition`**

In `fraud/community_detector.py`, add `LOUVAIN_ENGINE` to the `from config import (...)` block. Then add, right before the `CommunityDetector` class:

```python
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
```

- [ ] **Step 6: Refactor `run()` to dispatch through `partition_graph`**

In `CommunityDetector.run()`, replace the inline Louvain call:

```python
        raw_communities = nx.community.louvain_communities(
            graph,
            weight="weight",
            resolution=LOUVAIN_RESOLUTION,
            seed=LOUVAIN_SEED,
        )
```

with:

```python
        raw_communities = partition_graph(graph)
```

(The `split_disconnected(raw_communities, graph)` line directly below it stays — it is the safety net for the networkx engine and a no-op for Leiden.)

- [ ] **Step 7: Install deps, run both engine paths and the full suite**

```bash
pip install igraph==1.0.0 leidenalg==0.12.0
python -m pytest tests/test_community_detection.py -q
LOUVAIN_ENGINE=leiden python -m pytest tests/test_community_detection.py -k "detector or partition_graph" -q
```

Expected: first run 41 passed; second run green (orchestration + dispatch tests pass under the Leiden engine too).

- [ ] **Step 8: Commit**

```bash
git add requirements.txt config.py fraud/community_detector.py tests/test_community_detection.py
git commit -m "feat: pluggable Leiden engine behind LOUVAIN_ENGINE (networkx default)"
```

---

### Task 9: Manual entrypoint (gather-scatter demo)

**Files:**
- Modify: `fraud/community_detector.py` (append demo + `__main__` block)

**Interfaces:**
- Consumes: `CommunityDetector`, `Neo4jClient`, `PostgresClient`, migration 002.
- Produces: `python -m fraud.community_detector` — seeds a known gather-scatter cluster and prints the resulting flag. Manual verification only; no test file (the orchestration is covered by Task 7, the DB methods by Task 6).

- [ ] **Step 1: Implement the demo entrypoint**

Append to `fraud/community_detector.py`:

```python
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
```

- [ ] **Step 2: Run the demo against docker compose**

```bash
docker compose -f ../docker-compose.yml up -d neo4j postgres
python -m fraud.community_detector
```

Expected output: seeding lines, then `Communities kept: >= 1`, an assignment count ≥ 8, and one `COMMUNITY flag` block whose members include `DEMO_LV_HUB` with a non-empty explanation. (Score lands ~0.53 medium: size 1.0, density saturated, volume ~$316k mid-scale, cohesion 1.0 (isolated cluster → conductance 0), overlap 0.) If the shared database contains benchmark data, extra communities/flags may print — the demo cluster's flag is the one containing `DEMO_LV_HUB`.

- [ ] **Step 3: Verify idempotency — run it again**

```bash
python -m fraud.community_detector
psql "$DATABASE_URL" -c "SELECT flag_type, detection_count, risk_level FROM risk_flags WHERE 'DEMO_LV_HUB' = ANY(account_ids);"
```

Expected: one row, `flag_type=COMMUNITY`, `detection_count=2` (upsert bumped, no duplicate). If `psql`/`DATABASE_URL` is unavailable, verify via python: `python -c "import asyncio; from db.postgres import PostgresClient; ..."` or just confirm the second run prints the same single flag.

- [ ] **Step 4: Run the full suite, then commit**

```bash
python -m pytest tests/ -m "not integration" -q
git add fraud/community_detector.py
git commit -m "feat: manual gather-scatter demo entrypoint for community detection"
```

---

### Task 10: IBM AML benchmark for community detection

**Files:**
- Modify: `benchmarks/ibm_aml/patterns.py` (generalize the parser)
- Create: `benchmarks/ibm_aml/louvain_runner.py`

**Interfaces:**
- Consumes: `CycleGroup` dataclass, `_build_group_from_rows`, `_row_from_parts`, `_parse_ts` (all in `patterns.py`); `ingest()` from `benchmarks/ibm_aml/ingestor.py` (it only reads `.accounts` off each group, so generalized groups pass straight through); `CommunityDetector` from Task 7.
- Produces:
  - `patterns.load_pattern_groups(patterns_path: str | Path, typologies: Iterable[str]) -> list[CycleGroup]` — parses any of the 8 labeled typologies (`CYCLE`, `FAN-IN`, `FAN-OUT`, `GATHER-SCATTER`, `SCATTER-GATHER`, `BIPARTITE`, `STACK`, `RANDOM`). `CycleGroup` gains a `typology: str = "CYCLE"` field (defaulted — existing callers unaffected).
  - `python -m benchmarks.ibm_aml.louvain_runner` CLI producing a JSON report in `benchmarks/results/`.

- [ ] **Step 1: Generalize the patterns parser**

In `benchmarks/ibm_aml/patterns.py`:

1. Add a `typology` field to `CycleGroup`. It must go **after** `raw_rows` (the last field) — every existing field except `raw_rows` lacks a default, and dataclass rules require defaulted fields to come last:

```python
    raw_rows: list[dict] = field(default_factory=list, repr=False)
    typology: str = "CYCLE"       # laundering typology label from the patterns file
```

2. Change `_build_group_from_rows` to accept and pass the typology:

```python
def _build_group_from_rows(
    group_id: int, rows: list[dict], typology: str = "CYCLE"
) -> Optional[CycleGroup]:
```

and in its `return CycleGroup(...)` add `typology=typology,`.

3. Change `_parse_patterns_file` to take a typology filter. Replace the signature and the `in_cycle` logic:

```python
def _parse_patterns_file(
    patterns_path: Path,
    typologies: frozenset = frozenset({"CYCLE"}),
) -> list[CycleGroup]:
```

Inside the function, rename every use of `in_cycle` → `in_target`, add a `current_typology` state variable, and replace the state setup, `_flush` closure, and BEGIN branch with:

```python
    group_id = 0
    in_target = False
    current_typology = "CYCLE"
    current_rows: list[dict] = []

    _BEGIN_RE = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([A-Z\-]+)", re.IGNORECASE)
    _END_RE   = re.compile(r"^END LAUNDERING ATTEMPT",                 re.IGNORECASE)

    def _flush():
        nonlocal group_id, current_rows, in_target
        if in_target and current_rows:
            g = _build_group_from_rows(group_id, current_rows, current_typology)
            if g:
                groups.append(g)
                group_id += 1
        current_rows = []
        in_target = False
```

and in the line-reading loop:

```python
            begin_m = _BEGIN_RE.match(stripped)
            if begin_m:
                _flush()
                typology = begin_m.group(1).upper().rstrip(":")
                typology_counts[typology] = typology_counts.get(typology, 0) + 1
                in_target = typology in typologies
                current_typology = typology
                continue
```

The data-row branch keeps its shape, becoming `if in_target:`. The docstring's "Other typologies … are skipped" note should be updated to say the filter is the `typologies` argument (default `{"CYCLE"}` preserves `load_cycle_groups` behavior exactly).

4. Add the public loader at module bottom:

```python
def load_pattern_groups(patterns_path, typologies) -> "list[CycleGroup]":
    """
    Load labeled laundering groups for any set of typologies.

    Unlike load_cycle_groups there is no CSV-derived fallback: non-cycle
    typologies have no structural signature we could derive from the
    transactions file alone, so a missing/unparseable patterns file returns [].

    Args:
        patterns_path: HI-Small_Patterns.txt
        typologies:    e.g. ["GATHER-SCATTER", "BIPARTITE"] (case-insensitive)
    """
    wanted = frozenset(t.strip().upper() for t in typologies)
    path = Path(patterns_path)
    if not path.exists():
        logger.warning("Patterns file %s not found — no groups loaded", path)
        return []
    return _parse_patterns_file(path, wanted)
```

5. Verify `load_cycle_groups` still passes `frozenset({"CYCLE"})` semantics (its internal call becomes `_parse_patterns_file(path)` using the default) and existing tests stay green:

```bash
python -m pytest tests/ -m "not integration" -q
```

Expected: PASS, no regressions.

- [ ] **Step 2: Write the benchmark runner**

Create `benchmarks/ibm_aml/louvain_runner.py`:

```python
"""
IBM AML benchmark runner for Louvain community detection.

Validates the community detector against the labeled NON-cycle typologies
(gather-scatter, scatter-gather, bipartite, stack, random, fan-in, fan-out) —
the structures cycle detection is blind to by design.

Pipeline:
  1. Parse labeled groups for the target typologies from the patterns file
  2. Ingest their transactions + background sample into Neo4j (reuses the
     production writer via benchmarks.ibm_aml.ingestor.ingest)
  3. Run CommunityDetector.run() once, anchored to the dataset's max timestamp
  4. Recall:    a group counts as detected when a single flagged community
                contains >= --containment (default 0.5) of the group's accounts
  5. Precision: a flagged community counts as a true positive when
                >= --precision-overlap (default 0.25) of its members are
                labeled laundering accounts (ANY typology, cycles included)
  6. Print report + write JSON to benchmarks/results/

Usage:
    LOUVAIN_WINDOW_DAYS=60 python -m benchmarks.ibm_aml.louvain_runner \\
        --csv      benchmarks/data/HI-Small_Trans.csv \\
        --patterns benchmarks/data/HI-Small_Patterns.txt \\
        --background-ratio 5.0 \\
        --report   benchmarks/results/louvain_$(date +%Y%m%d).json

Notes:
  - LOUVAIN_WINDOW_DAYS must cover the ~30-day dataset span; 60 is safe.
  - Without --with-postgres the overlap dimension is 0 for every community
    (max achievable composite 0.65). Run the cycle benchmark first WITH
    postgres, then pass --with-postgres here, to measure the cross-detector
    corroboration lift.
  - Use --skip-ingest on re-runs against an already-loaded Neo4j.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from benchmarks.ibm_aml.ingestor import ingest
from benchmarks.ibm_aml.patterns import _parse_ts, load_pattern_groups
from db.neo4j import Neo4jClient
from db.postgres import PostgresClient
from fraud.community_detector import CommunityDetector

logger = logging.getLogger(__name__)

ALL_TYPOLOGIES = [
    "CYCLE", "FAN-IN", "FAN-OUT", "GATHER-SCATTER",
    "SCATTER-GATHER", "BIPARTITE", "STACK", "RANDOM",
]
DEFAULT_TARGETS = "GATHER-SCATTER,SCATTER-GATHER,BIPARTITE,STACK,RANDOM,FAN-IN,FAN-OUT"


def _dataset_max_ts(groups) -> datetime:
    """Anchor detection to the dataset's own clock, not wall time."""
    max_ts = None
    for g in groups:
        for row in g.raw_rows:
            ts = _parse_ts(row.get("Timestamp", ""))
            if ts and (max_ts is None or ts > max_ts):
                max_ts = ts
    if max_ts is None:
        raise SystemExit("No parseable timestamps in loaded groups — cannot anchor window")
    return max_ts


def score_recall(groups, flags, containment: float):
    """Per-typology TP/FN. A group is detected when one flagged community
    contains >= containment of the group's accounts."""
    flag_member_sets = [set(f["account_ids"]) for f in flags]
    per_typology: dict = {}
    for g in groups:
        bucket = per_typology.setdefault(g.typology, {"tp": 0, "fn": 0, "groups": 0})
        bucket["groups"] += 1
        accounts = set(g.accounts)
        best = max(
            (len(accounts & members) / len(accounts) for members in flag_member_sets),
            default=0.0,
        )
        if best >= containment:
            bucket["tp"] += 1
        else:
            bucket["fn"] += 1
    return per_typology


def score_precision(flags, labeled_accounts: set, min_overlap: float):
    """Fraction of flagged communities that substantially overlap labeled
    laundering accounts of ANY typology."""
    if not flags:
        return {"tp": 0, "fp": 0, "precision": None}
    tp = sum(
        1 for f in flags
        if len(set(f["account_ids"]) & labeled_accounts) / len(f["account_ids"]) >= min_overlap
    )
    fp = len(flags) - tp
    return {"tp": tp, "fp": fp, "precision": tp / len(flags)}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--patterns", required=True)
    parser.add_argument("--typologies", default=DEFAULT_TARGETS,
                        help="Comma-separated recall targets")
    parser.add_argument("--background-ratio", type=float, default=5.0)
    parser.add_argument("--containment", type=float, default=0.5,
                        help="Group-account fraction one community must contain")
    parser.add_argument("--precision-overlap", type=float, default=0.25,
                        help="Labeled-member fraction for a flag to count as TP")
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--with-postgres", action="store_true",
                        help="Persist flags + use cross-detector overlap scoring")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    targets = [t.strip().upper() for t in args.typologies.split(",") if t.strip()]
    target_groups = load_pattern_groups(args.patterns, targets)
    all_groups = load_pattern_groups(args.patterns, ALL_TYPOLOGIES)
    labeled_accounts = {a for g in all_groups for a in g.accounts}
    logger.info("Loaded %d target groups (%s); %d labeled accounts overall",
                len(target_groups), ",".join(targets), len(labeled_accounts))
    if not target_groups:
        raise SystemExit("No labeled groups for the requested typologies")

    neo4j = Neo4jClient()
    await neo4j.initialize()
    await neo4j.init_constraints()
    postgres = None
    if args.with_postgres:
        postgres = PostgresClient()
        await postgres.initialize()

    if not args.skip_ingest:
        stats = await ingest(
            args.csv, target_groups, neo4j_client=neo4j,
            background_ratio=args.background_ratio,
        )
        logger.info("Ingest done: %s", stats)

    reference_time = _dataset_max_ts(target_groups)
    logger.info("Detection anchored to dataset max timestamp: %s", reference_time)

    t0 = time.monotonic()
    detector = CommunityDetector(neo4j, postgres)
    result = await detector.run(reference_time=reference_time)
    runtime_s = time.monotonic() - t0

    recall = score_recall(target_groups, result["flags"], args.containment)
    total_tp = sum(b["tp"] for b in recall.values())
    total_groups = sum(b["groups"] for b in recall.values())
    precision = score_precision(result["flags"], labeled_accounts, args.precision_overlap)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(runtime_s, 2),
        "config": {
            "typologies": targets,
            "containment": args.containment,
            "precision_overlap": args.precision_overlap,
            "with_postgres": args.with_postgres,
        },
        "communities_kept": result["communities"],
        "flags": len(result["flags"]),
        "recall_overall": round(total_tp / total_groups, 4) if total_groups else None,
        "recall_by_typology": {
            t: {**b, "recall": round(b["tp"] / b["groups"], 4)}
            for t, b in sorted(recall.items())
        },
        "precision": precision,
    }

    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2))
        logger.info("Report written to %s", args.report)

    await neo4j.close()
    if postgres:
        await postgres.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Smoke-test the loader against the real patterns file**

```bash
python -c "
from benchmarks.ibm_aml.patterns import load_pattern_groups
gs = load_pattern_groups('benchmarks/data/HI-Small_Patterns.txt', ['GATHER-SCATTER'])
print(len(gs), gs[0].typology, len(gs[0].accounts))"
```

Expected: `51 GATHER-SCATTER <N>` (51 gather-scatter blocks exist in HI-Small_Patterns.txt; N > 2).

- [ ] **Step 4: Run the benchmark end to end**

```bash
docker compose -f ../docker-compose.yml up -d neo4j
LOUVAIN_WINDOW_DAYS=60 python -m benchmarks.ibm_aml.louvain_runner \
    --csv benchmarks/data/HI-Small_Trans.csv \
    --patterns benchmarks/data/HI-Small_Patterns.txt \
    --background-ratio 5.0 \
    --report benchmarks/results/louvain_baseline.json
```

Expected: JSON report printed with non-null `recall_overall` and `precision`. **Record the baseline numbers in the commit message.** Do not tune knobs in this task — the baseline is the deliverable; tuning is its own follow-up (the cycle work iterated 0% → 87% across separate commits).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/ibm_aml/patterns.py benchmarks/ibm_aml/louvain_runner.py
git commit -m "feat: IBM AML benchmark for louvain — baseline recall=<X>% precision=<Y>%"
```

---

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything shipped in Tasks 1–10.
- Produces: CLAUDE.md that accurately describes the Louvain layer for future sessions.

- [ ] **Step 1: Update CLAUDE.md**

Make these edits (adapt to the file's current section wording on the rebased branch):

1. **Current status** — move Louvain out of "Not started": under the graph-algorithm bullet list, mark Louvain clustering as done alongside cycle detection, e.g. `- Louvain community detection (daily batch, networkx/Leiden) — done, benchmarked on IBM AML`.

2. **Key patterns** — append a paragraph after the cycle-detection/graph-algorithms pattern:

```markdown
**Community detection (Louvain/Leiden)** — daily batch over aggregate `FLOWS_TO`
edges active in the last `LOUVAIN_WINDOW_DAYS` (default 30). Partitioning runs
Python-side behind `LOUVAIN_ENGINE`: seeded `networkx.community.louvain_communities`
by default (no extra dependency), or `leidenalg`/`igraph` Leiden as an opt-in
engine that guarantees internally-connected communities and scales better (no GDS
plugin either way). Edge weight is `log1p(total_amount)` (`LOUVAIN_WEIGHT_MODE`).
Every community is split into connected components before scoring (fixes Louvain's
disconnected-community defect). Communities are scored on five dimensions — size
band, density, internal volume, isolation (`1 − conductance`), and overlap with
accounts already flagged by other detectors — and those at/above medium persist to
`risk_flags` as `flag_type='COMMUNITY'`, fingerprinted on their top-K
weighted-degree core so daily re-detection upserts instead of duplicating.
All kept communities are also written onto `Account` nodes as `community_id` /
`community_detected_at`. Entrypoint: `python -m fraud.community_detector`
(scheduling is a deploy concern — cron; not wired in-process).
```

3. **Conventions** — add one bullet documenting the batch-write exception decided in design review:

```markdown
- The outbox rule applies to *payment events*. Batch algorithm jobs (cycle
  detection, Louvain) read Neo4j directly and may write derived analytical
  node properties (e.g. `community_id`) directly to Neo4j — never payment
  edges, and always with a `*_detected_at` provenance timestamp.
```

4. **Data model** — in the Neo4j nodes description, add `community_id` and `community_detected_at` to the account-node property list.

- [ ] **Step 2: Self-check the docs against the code**

```bash
grep -n "community" CLAUDE.md
python -m pytest tests/ -m "not integration" -q
```

Expected: all four edits present; suite green.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: louvain community detection — status, batch-write pattern, knobs"
```

---

## Follow-ups (explicitly out of scope)

- Cron/scheduler wiring for the daily batch (same follow-up status as the cycle detector's live wiring).
- Knob tuning against the benchmark baseline (window, resolution, weight mode, thresholds, containment). Research (GARG-AML) suggests **unweighted** partitioning with a high resolution may beat `log_amount`; the `LOUVAIN_WEIGHT_MODE` / `LOUVAIN_RESOLUTION` / `LOUVAIN_ENGINE` knobs let the benchmark settle this empirically — no external per-typology baseline exists for non-cycle typologies on HI-Small, so our runner produces a first-of-its-kind number, not a "beat state-of-the-art" target.
- Alternative flow-normalized edge weight `W(s→t) = amount/total_sent(s) + amount/total_received(t)` (harder for a launderer to game than raw/log amount) — add as a new `LOUVAIN_WEIGHT_MODE` if tuning stalls. (Low-confidence source; validate before adopting.)
- Temporal burstiness as a 6th scoring dimension (HoloScope-style): cheap proxy is `max(daily_internal_tx) / mean(daily_internal_tx)` over the window, computable from the Redis `edge:{a}:{b}` ZSETs we already keep. Distinguishes a newly-formed ring from a long-standing legitimate high-volume cluster.
- Jaccard-overlap flag matching if core-hash churn produces duplicate alerts in practice.
- Server-side **GDS `gds.leiden`** as the large-graph escape hatch: GDS Community Edition installs free on the plain `neo4j:5` image (`NEO4J_PLUGINS='["graph-data-science"]'`, concurrency capped at 4). The Python client must be pinned to **`graphdatascience==1.17`** — current releases require Python ≥3.10, which our faust pin forbids. Only needed if the whole-graph-in-memory export becomes the ceiling.
- **AMLGentex** (github.com/aidotse/AMLGentex, preferred over the older IBM AMLSim) as an external, offline generator for additional labeled non-cycle typology scenarios. Runs in its own Python ≥3.10 environment and produces CSVs — never imported into this Py3.9 service.
- Incremental PageRank (separate feature, separate plan).
