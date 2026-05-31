import json
import logging

import faust

from config import (
    KAFKA_BROKER,
    TOPIC_ACH_RAW,
    TOPIC_CARD_RAW,
    TOPIC_CRYPTO_RAW,
    TOPIC_NORMALIZED,
    TOPIC_WIRE_RAW,
)
from normalizer.ach_normalizer import normalize_ach
from normalizer.card_normalizer import normalize_card
from normalizer.crypto_normalizer import normalize_crypto
from normalizer.wire_normalizer import normalize_wire

logger = logging.getLogger(__name__)

app = faust.App(
    "flowgraph",
    broker=KAFKA_BROKER,
    value_serializer="raw",
)

# Raw inbound topics — Faust assigns each agent its own consumer group automatically:
#   flowgraph-process_card, flowgraph-process_ach, etc.
# Partition keys are set by upstream producers, not by us — see config.py for the
# per-rail key contract.
card_raw_topic   = app.topic(TOPIC_CARD_RAW,   value_type=bytes)
ach_raw_topic    = app.topic(TOPIC_ACH_RAW,    value_type=bytes)
wire_raw_topic   = app.topic(TOPIC_WIRE_RAW,   value_type=bytes)
crypto_raw_topic = app.topic(TOPIC_CRYPTO_RAW, value_type=bytes)

# All rails land here after normalization, keyed by sender_id so downstream
# consumers see a consistent partition key regardless of rail
normalized_topic = app.topic(TOPIC_NORMALIZED, value_type=bytes)


@app.agent(card_raw_topic)
async def process_card(stream):
    async for key, raw in stream.items():
        try:
            event = normalize_card(json.loads(raw))
            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("card normalization failed, key=%r", key)


@app.agent(ach_raw_topic)
async def process_ach(stream):
    async for key, raw in stream.items():
        try:
            event = normalize_ach(json.loads(raw))
            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("ach normalization failed, key=%r", key)


@app.agent(wire_raw_topic)
async def process_wire(stream):
    async for key, raw in stream.items():
        try:
            event = normalize_wire(json.loads(raw))
            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("wire normalization failed, key=%r", key)


@app.agent(crypto_raw_topic)
async def process_crypto(stream):
    async for key, raw in stream.items():
        try:
            event = normalize_crypto(json.loads(raw))
            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("crypto normalization failed, key=%r", key)
