import os

# ==================== KAFKA CONFIGURATION ====================
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka://localhost:9092")
KAFKA_CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "flowgraph-payment-processor")

# Payment event topics (by rail)
TOPIC_CARD_RAW = os.getenv("TOPIC_CARD_RAW", "payments.raw.card")
TOPIC_WIRE_RAW = os.getenv("TOPIC_WIRE_RAW", "payments.raw.wire")
TOPIC_ACH_RAW = os.getenv("TOPIC_ACH_RAW", "payments.raw.ach")
TOPIC_CRYPTO_RAW = os.getenv("TOPIC_CRYPTO_RAW", "payments.raw.crypto")

# Dead-letter queues (for malformed events)
TOPIC_CARD_DLQ = os.getenv("TOPIC_CARD_DLQ", "payments.raw.card.dlq")
TOPIC_WIRE_DLQ = os.getenv("TOPIC_WIRE_DLQ", "payments.raw.wire.dlq")
TOPIC_ACH_DLQ = os.getenv("TOPIC_ACH_DLQ", "payments.raw.ach.dlq")
TOPIC_CRYPTO_DLQ = os.getenv("TOPIC_CRYPTO_DLQ", "payments.raw.crypto.dlq")

# ==================== DATABASE CONFIGURATION ====================
# PostgreSQL (canonical transaction records + outbox table)
POSTGRES_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql+asyncpg://flowgraph:flowgraph@localhost:5432/flowgraph"
)
POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "20"))
POSTGRES_POOL_TIMEOUT = int(os.getenv("POSTGRES_POOL_TIMEOUT", "30"))

# Neo4j (property graph: accounts, merchants, edges, centrality)
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# Redis (time-windowed edge weights, caching)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_ENCODING = "utf-8"

# ==================== OUTBOX PATTERN CONFIGURATION ====================
# Background worker polls outbox table on this interval
OUTBOX_POLL_INTERVAL_SECONDS = int(os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "5"))

# Batch size for each poll (process N records per cycle)
OUTBOX_BATCH_SIZE = int(os.getenv("OUTBOX_BATCH_SIZE", "50"))

# Maximum retry attempts before marking as failed
OUTBOX_MAX_RETRIES = int(os.getenv("OUTBOX_MAX_RETRIES", "3"))

# Retry backoff: retry_count * OUTBOX_RETRY_INTERVAL_SECONDS
OUTBOX_RETRY_INTERVAL_SECONDS = int(os.getenv("OUTBOX_RETRY_INTERVAL_SECONDS", "10"))

# Table name (can be overridden for testing)
OUTBOX_TABLE_NAME = os.getenv("OUTBOX_TABLE_NAME", "outbox")
TRANSACTIONS_TABLE_NAME = os.getenv("TRANSACTIONS_TABLE_NAME", "transactions")

# ==================== METRICS & MONITORING ====================
# Metrics reporter logs stats every N seconds
METRICS_REPORT_INTERVAL_SECONDS = int(os.getenv("METRICS_REPORT_INTERVAL_SECONDS", "60"))

# ==================== LOGGING ====================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ==================== FRAUD DETECTION ====================
# Cycle detection — transaction-level DFS over TRANSFER edges.
# All values are tunable via env vars; defaults reflect CLAUDE.md spec.

CYCLE_MAX_DEPTH = int(os.getenv("CYCLE_MAX_DEPTH", "6"))
# Max hops in a cycle (6 = safe Neo4j traversal limit; increase cautiously)

CYCLE_WINDOW_HOURS = int(os.getenv("CYCLE_WINDOW_HOURS", "48"))
# Look-back window: only consider TRANSFER edges within this many hours

CYCLE_MAX_HOP_GAP_HOURS = float(os.getenv("CYCLE_MAX_HOP_GAP_HOURS", "72.0"))
# Max hours allowed between consecutive hops (72h = 3 days; real layering
# schemes often spread hops over days to avoid detection)

CYCLE_MAX_LEAK = float(os.getenv("CYCLE_MAX_LEAK", "0.20"))
# Max fractional value bleed-off per hop (0.20 = up to 20% fees/cuts allowed)

CYCLE_CONSERVATION_MODE = os.getenv("CYCLE_CONSERVATION_MODE", "hop").lower()
# How to enforce amount conservation on aggregate FLOWS_TO cycles:
#   "hop"   — per-hop range-overlap, skipping cross-currency hops. Best F1 on IBM AML
#             (100% precision, 68.5% recall): correctly rejects coincidental rings that
#             "off" lets through, and skip-cross-currency avoids FX numeric mismatch.
#             Default.
#   "cycle" — whole-ring magnitude consistency (weakest hop >= strongest × (1-max_cycle_leak)).
#             Worse on multi-currency data: raw-cent magnitudes differ across currencies
#             (Yuan cents ~7x USD cents for equal value), so FX trips the whole-ring check.
#   "off"   — topology + temporal + value floor only. Highest raw recall (70.4%) but
#             precision drops to 84% (coincidental rings). Use when the other detectors
#             (PageRank hubs, Louvain communities) own precision.

CYCLE_MAX_CYCLE_LEAK = float(os.getenv("CYCLE_MAX_CYCLE_LEAK", "0.60"))
# For conservation_mode="cycle": max total magnitude spread across the whole ring.
# 0.60 = weakest hop may be down to 40% of the strongest (accumulated fees/splits
# around a multi-hop loop) and still count as one conserved flow.

CYCLE_MIN_VALUE_CENTS = int(os.getenv("CYCLE_MIN_VALUE_CENTS", "10000"))
# Minimum weakest-hop amount to flag ($100 — filters trivial test noise while
# catching structuring below $1k thresholds)

CYCLE_MAX_RESULTS = int(os.getenv("CYCLE_MAX_RESULTS", "20"))
# Cap on cycles returned per account per detection run

CYCLE_QUERY_TIMEOUT_SECONDS = float(os.getenv("CYCLE_QUERY_TIMEOUT_SECONDS", "10.0"))
# Per-account cycle query transaction timeout. A fraud query must never hang the
# pipeline; a timed-out search returns no cycle (correct for a miss) and bounds latency.
# Raise for deep batch sweeps (depth 12), keep low (5-10s) for real-time streaming.

CYCLE_FAST_CLOSE_HOURS = float(os.getenv("CYCLE_FAST_CLOSE_HOURS", "24.0"))
# Velocity scoring knee: loops closing faster than this score higher for velocity

# Score thresholds → risk level (lower-bound, inclusive)
CYCLE_LEVEL_MEDIUM = float(os.getenv("CYCLE_LEVEL_MEDIUM", "0.40"))
CYCLE_LEVEL_HIGH = float(os.getenv("CYCLE_LEVEL_HIGH", "0.65"))
CYCLE_LEVEL_CRITICAL = float(os.getenv("CYCLE_LEVEL_CRITICAL", "0.85"))


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

LOUVAIN_MIN_EDGE_TX_COUNT = int(os.getenv("LOUVAIN_MIN_EDGE_TX_COUNT", "20"))
# Minimum combined tx_count (both directions, already summed by
# build_undirected_graph) an account pair needs before its edge joins
# Louvain's input graph. A single one-off transaction is as weak a
# same-community signal as a shared coffee-shop IP in an identity graph —
# cheap for Louvain to bridge two otherwise-unrelated dense regions with.
# On the IBM AML HI-Small benchmark, 96.5% of flagged communities at the
# unfiltered default (1) contained ZERO labeled fraud accounts of any
# typology; they were background accounts stitched together by chains of
# tx_count<=2 edges. Tuned via sweep over benchmarks/results/: F1 rises from
# 1.27% (untuned) to 46.44% at 20, recall trading from 81.65% to 32.59% along
# the way. Set to 1 to disable filtering.

# --- Community scoring knobs ---
LOUVAIN_DENSITY_REF = float(os.getenv("LOUVAIN_DENSITY_REF", "0.6"))
# Internal edge density (2m / n(n-1)) at which density_score saturates to 1.0.
# 0.15 saturated for free: a bare connected spanning tree (the minimum
# possible density for a connected community) already clears it for any n
# up to ~14, so density_score couldn't distinguish a mesh from a chain at
# the community sizes this scorer sees most. Raised via sweep on top of the
# LOUVAIN_MIN_EDGE_TX_COUNT filter (see that knob) -- see commit history for
# the before/after benchmark numbers.

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

LOUVAIN_ENGINE = os.getenv("LOUVAIN_ENGINE", "networkx").lower()
# Community-detection engine:
#   "networkx" — pure-Python networkx Louvain, zero extra dependencies. Default.
#   "leiden"   — leidenalg/igraph Leiden: C/C++ core (faster on large graphs),
#                communities internally connected by construction. Requires the
#                optional igraph + leidenalg (GPL) packages to be installed.