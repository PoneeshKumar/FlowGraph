import os

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka://localhost:9092")

# PostgreSQL
POSTGRES_HOST     = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT     = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB       = os.getenv("POSTGRES_DB", "flowgraph")
POSTGRES_USER     = os.getenv("POSTGRES_USER", "flowgraph")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme")

# Neo4j
NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Outbox recovery worker
OUTBOX_TICK_SECONDS  = float(os.getenv("OUTBOX_TICK_SECONDS", "30"))
OUTBOX_CLAIM_SECONDS = int(os.getenv("OUTBOX_CLAIM_SECONDS", "60"))

# Raw inbound topics — one per rail
# Partition key is set by the upstream producer (bank API, card network, etc.):
#   payments.raw.card   → key: card_id      auth + settlement must land on the same partition
#   payments.raw.ach    → key: account_id   batch files must be processed in order per account
#   payments.raw.wire   → key: sender_id    cycle detection requires ordering per sender
#   payments.raw.crypto → key: wallet_addr  same ordering requirement as wire
TOPIC_CARD_RAW   = "payments.raw.card"
TOPIC_ACH_RAW    = "payments.raw.ach"
TOPIC_WIRE_RAW   = "payments.raw.wire"
TOPIC_CRYPTO_RAW = "payments.raw.crypto"

# All rails collapse here after normalization — partition key: sender_id
# Downstream consumers (graph writer, fraud engine, AI enrichment) subscribe only here
# and branch on the rail discriminator field when they need rail-specific behavior
TOPIC_NORMALIZED = "payments.normalized"
