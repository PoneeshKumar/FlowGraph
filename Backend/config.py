import os

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka://localhost:9092")

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
