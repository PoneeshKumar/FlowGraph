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

CYCLE_FAST_CLOSE_HOURS = float(os.getenv("CYCLE_FAST_CLOSE_HOURS", "24.0"))
# Velocity scoring knee: loops closing faster than this score higher for velocity

# Score thresholds → risk level (lower-bound, inclusive)
CYCLE_LEVEL_MEDIUM = float(os.getenv("CYCLE_LEVEL_MEDIUM", "0.40"))
CYCLE_LEVEL_HIGH = float(os.getenv("CYCLE_LEVEL_HIGH", "0.65"))
CYCLE_LEVEL_CRITICAL = float(os.getenv("CYCLE_LEVEL_CRITICAL", "0.85"))