import os

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka://localhost:9092")
TOPIC_CARD_RAW = os.getenv("TOPIC_CARD_RAW", "payments.raw.card")
TOPIC_WIRE_RAW = os.getenv("TOPIC_WIRE_RAW", "payments.raw.wire")
TOPIC_ACH_RAW = os.getenv("TOPIC_ACH_RAW", "payments.raw.ach")
