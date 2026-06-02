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
from models.card_events import CardAuthEvent, CardSettlementEvent, EventType
from fx import to_usd_cents

logger = logging.getLogger(__name__)

app = faust.App(
    "flowgraph",
    broker=KAFKA_BROKER,
    value_serializer="raw",
    topic_allow_declare=False,
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
            data = json.loads(raw)
            # canonical USD is required on the base model — compute it before
            # validation since raw card payloads don't carry it
            data.setdefault(
                "amount_usd_cents",
                to_usd_cents(data["amount_cents"], data.get("currency", "CAD")),
            )
            if data.get("event_type") == EventType.AUTH.value:
                event = CardAuthEvent.model_validate(data)
            else:
                event = CardSettlementEvent.model_validate(data)
            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("card processing failed, key=%r", key)


@app.agent(ach_raw_topic)
async def process_ach(stream):
    async for key, raw in stream.items():
        logger.warning("ACH rail not yet implemented, dropping key=%r", key)


@app.agent(wire_raw_topic)
async def process_wire(stream):
    async for key, raw in stream.items():
        logger.warning("WIRE rail not yet implemented, dropping key=%r", key)


@app.agent(crypto_raw_topic)
async def process_crypto(stream):
    async for key, raw in stream.items():
        logger.warning("CRYPTO rail not yet implemented, dropping key=%r", key)
