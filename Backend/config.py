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
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
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