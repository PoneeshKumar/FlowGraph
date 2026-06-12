import json
import logging

import faust

from config import (
    KAFKA_BROKER,
    OUTBOX_CLAIM_SECONDS,
    OUTBOX_TICK_SECONDS,
    TOPIC_ACH_RAW,
    TOPIC_CARD_RAW,
    TOPIC_CRYPTO_RAW,
    TOPIC_NORMALIZED,
    TOPIC_WIRE_RAW,
)
from models.card_events import CardAuthEvent, CardSettlementEvent, EventType
from models.wire_events import WireEvent
from models.crypto_events import CryptoEvent, CryptoTxStatus
from models.ach_events import ACHCreditEvent, ACHDebitEvent, ACHEventType
from fx import to_usd_cents
from db import postgres, neo4j, redis as redis_db

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


@app.task
async def on_startup():
    # create the payments table and the :Event(id) uniqueness constraint once
    await postgres.init_schema()
    await neo4j.init_constraints()


async def drive_graph_and_cache(row: dict) -> None:
    """The shared replay unit. Called by process_normalized after the Postgres
    insert AND by the outbox worker for stuck rows, so both paths drive identical,
    idempotent writes. On success it clears the outbox flag; on failure it leaves
    the flag set for the outbox worker to retry."""
    await neo4j.upsert_payment_graph(row)
    await redis_db.zadd_edge(row)
    await postgres.clear_outbox(row["event_id"])


# ── The engine ──────────────────────────────────────────────────────────────
# 1. Postgres write first (source of truth, pending_graph_sync=TRUE). A redelivery
#    returns None (row already existed) -> skip graph/cache entirely.
# 2+3. Neo4j upsert + Redis cache (idempotent under replay).
# 4. clear_outbox on success. On any failure the flag stays set and the
#    @app.timer outbox worker re-drives the row.
@app.agent(normalized_topic)
async def process_normalized(stream):
    async for key, raw in stream.items():
        event_id = None
        try:
            event = json.loads(raw)
            event_id = str(event.get("event_id"))
            row = await postgres.write_payment(event)
            if row is None:
                continue  # redelivery — Postgres dedup gate
            await drive_graph_and_cache(row)
        except Exception:
            # do not re-raise: the Postgres row (if written) stays pending and
            # the outbox worker recovers it; the offset advances
            logger.exception("normalized processing failed, event_id=%s key=%r", event_id, key)


@app.timer(interval=OUTBOX_TICK_SECONDS)
async def outbox_recovery():
    try:
        rows = await postgres.claim_stuck_rows(older_than_seconds=OUTBOX_CLAIM_SECONDS)
        for row in rows:
            try:
                await drive_graph_and_cache(row)
            except Exception:
                logger.exception("outbox re-drive failed, event_id=%s", row.get("event_id"))
    except Exception:
        logger.exception("outbox recovery sweep failed")


# ── Rail agents ─────────────────────────────────────────────────────────────
# Each agent:
#   1. Deserializes the raw bytes from its rail-specific topic
#   2. Computes amount_usd_cents (canonical USD, required on BasePaymentEvent)
#   3. Validates through the rail's Pydantic model
#   4. Forwards the normalized event to payments.normalized keyed by sender_id
#
# Declined / failed events are still forwarded — the graph and compliance layer
# need visibility into them. The status field on the event carries the outcome.

@app.agent(card_raw_topic)
async def process_card(stream):
    async for key, raw in stream.items():
        try:
            data = json.loads(raw)
            # canonical USD is required on the base model — compute it before
            # validation since raw card payloads don't carry it
            data.setdefault(
                "amount_usd_cents",
                to_usd_cents(data["amount_cents"], data.get("currency", "USD")),
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
        try:
            data = json.loads(raw)
            data.setdefault(
                "amount_usd_cents",
                to_usd_cents(data["amount_cents"], data.get("currency", "USD")),
            )
            # Route to credit or debit model based on ach_event_type discriminator
            if data.get("ach_event_type") == ACHEventType.ACH_CREDIT.value:
                event = ACHCreditEvent.model_validate(data)
            else:
                event = ACHDebitEvent.model_validate(data)
            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("ach processing failed, key=%r", key)


@app.agent(wire_raw_topic)
async def process_wire(stream):
    async for key, raw in stream.items():
        try:
            data = json.loads(raw)
            data.setdefault(
                "amount_usd_cents",
                to_usd_cents(data["amount_cents"], data.get("currency", "USD")),
            )
            event = WireEvent.model_validate(data)
            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("wire processing failed, key=%r", key)


@app.agent(crypto_raw_topic)
async def process_crypto(stream):
    async for key, raw in stream.items():
        try:
            data = json.loads(raw)
            # Crypto amounts are in native units (wei/satoshis) — FX stub
            # treats them 1:1 for now. Real FX would need chain-aware pricing.
            data.setdefault(
                "amount_usd_cents",
                to_usd_cents(data["amount_cents"], data.get("currency", "USD")),
            )
            event = CryptoEvent.model_validate(data)

            # Only forward CONFIRMED and FAILED events to the normalized topic.
            # PENDING events are broadcast state — they haven't settled yet and
            # would create premature graph edges. The confirmation update that
            # follows carries the same event_id so the Postgres dedup gate
            # ensures exactly-once graph writes.
            if event.crypto_status == CryptoTxStatus.PENDING:
                logger.debug(
                    "crypto PENDING — holding until confirmed, tx_ref=%s",
                    event.tx_reference,
                )
                continue

            await normalized_topic.send(
                key=event.sender_id.encode(),
                value=event.model_dump_json().encode(),
            )
        except Exception:
            logger.exception("crypto processing failed, key=%r", key)