"""
Backend/generator/ach_generator.py

Produces fake ACHCreditEvent and ACHDebitEvent messages to payments.raw.ach.
ACH arrives in batches — fires a batch of 5-25 transactions every 60-120 seconds,
simulating overnight NACHA file submissions.

5% of transactions generate a return event after 30-90 seconds.

Partition key: sender_id (account hash).

Run:
    python -m generator.ach_generator
"""

import asyncio
import json
import logging
import random
import string
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Literal

from aiokafka import AIOKafkaProducer

from config import KAFKA_BROKER, TOPIC_ACH_RAW
from fx import to_usd_cents
from models.ach_events import (
    ACHCreditEvent,
    ACHDebitEvent,
    ACHEventType,
    BaseACHEvent,
    SECCode,
    hash_account_number,
)
from models.card_events import EventStatus, EventType, Rail

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Fake account + bank pools
# ---------------------------------------------------------------------------

# 20 fake sender accounts — (account_number, routing_number, name)
_ACCOUNTS = [
    {
        "account_number": f"ACC{i:010d}",
        "routing_number": f"02600{i:04d}",     # fake but 9 digits
        "name": f"Account Holder {i:02d}",
    }
    for i in range(1, 21)
]

# Pre-hash account identifiers
for _acct in _ACCOUNTS:
    _acct["account_hash"] = hash_account_number(
        _acct["account_number"], _acct["routing_number"]
    )

# 6 fake US banks
_BANKS = [
    {"name": "Chase Bank",        "routing": "021000021"},
    {"name": "Bank of America",   "routing": "026009593"},
    {"name": "Wells Fargo",       "routing": "121042882"},
    {"name": "Citibank",          "routing": "021000089"},
    {"name": "US Bank",           "routing": "091000022"},
    {"name": "TD Bank",           "routing": "031101266"},
]

# Return codes and reasons
_RETURN_CODES = {
    "R01": "Insufficient Funds",
    "R02": "Account Closed",
    "R03": "No Account / Unable to Locate Account",
    "R10": "Customer Advises Not Authorized",
}

# SEC code weighted distribution
_SEC_WEIGHTS = [
    (SECCode.PPD, 0.50),
    (SECCode.CIB, 0.20),
    (SECCode.WEB, 0.20),
    (SECCode.TEL, 0.10),
]


# ---------------------------------------------------------------------------
# Amount helper
# ---------------------------------------------------------------------------

def _random_amount_cents() -> int:
    roll = random.random()
    if roll < 0.70:
        return random.randint(10_000, 500_000)       # $100 - $5,000
    elif roll < 0.95:
        return random.randint(500_001, 5_000_000)    # $5,000 - $50,000
    else:
        return random.randint(5_000_001, 50_000_000) # $50,000 - $500,000


def _random_sec_code() -> SECCode:
    codes, weights = zip(*_SEC_WEIGHTS)
    return random.choices(codes, weights=weights, k=1)[0]


def _generate_trace_number(odfi_routing: str, sequence: int) -> str:
    """
    NACHA trace number: first 8 digits = ODFI routing prefix,
    last 7 digits = sequence number within batch.
    Total: 15 digits.
    """
    prefix = odfi_routing[:8]
    seq    = str(sequence % 10_000_000).zfill(7)
    return f"{prefix}{seq}"


def _expected_settlement(submission_time: datetime) -> date:
    """T+1 to T+3 business days from submission."""
    days = random.randint(1, 3)
    return (submission_time + timedelta(days=days)).date()


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _build_ach_event(
    ach_event_type: ACHEventType,
    sender: dict,
    receiver: dict,
    odfi: dict,
    rdfi: dict,
    batch_id: str,
    batch_submission_time: datetime,
    trace_number: str,
    amount_cents: int,
    sec_code: SECCode,
) -> ACHCreditEvent | ACHDebitEvent:

    expected_date = _expected_settlement(batch_submission_time)

    base_fields = dict(
        rail=Rail.ACH,
        event_type=EventType.SETTLEMENT,
        sender_id=sender["account_hash"],
        receiver_id=receiver["account_hash"],
        sender_account_hash=sender["account_hash"],
        receiver_account_hash=receiver["account_hash"],
        amount_cents=amount_cents,
        amount_usd_cents=to_usd_cents(amount_cents, "USD"),
        currency="USD",
        timestamp_utc=batch_submission_time,
        ach_event_type=ach_event_type,
        trace_number=trace_number,
        batch_id=batch_id,
        batch_submission_time=batch_submission_time,
        odfi_routing=odfi["routing"],
        rdfi_routing=rdfi["routing"],
        sec_code=sec_code,
        expected_settlement_date=expected_date,
        raw_payload={
            "sender_name": sender["name"],
            "receiver_name": receiver["name"],
            "odfi_name": odfi["name"],
            "rdfi_name": rdfi["name"],
            "source": "ach_generator",
        },
    )

    if ach_event_type == ACHEventType.ACH_CREDIT:
        return ACHCreditEvent(**base_fields)
    return ACHDebitEvent(**base_fields)


def _build_return_event(
    original: ACHCreditEvent | ACHDebitEvent,
    return_code: str,
    return_reason: str,
) -> ACHCreditEvent | ACHDebitEvent:
    """Build a return event based on the original transaction."""
    now = datetime.now(timezone.utc)
    fields = original.model_dump()
    fields.update(
        event_id=str(uuid.uuid4()),     # new event_id for the return
        timestamp_utc=now,
        batch_submission_time=now,
        return_code=return_code,
        return_reason=return_reason,
        status=EventStatus.DECLINED,
        raw_payload={
            **fields.get("raw_payload", {}),
            "return_of": str(original.event_id),
            "return_code": return_code,
        },
    )
    if original.ach_event_type == ACHEventType.ACH_CREDIT:
        return ACHCreditEvent(**fields)
    return ACHDebitEvent(**fields)


# ---------------------------------------------------------------------------
# Kafka producer
# ---------------------------------------------------------------------------

async def _make_producer() -> AIOKafkaProducer:
    broker = KAFKA_BROKER.replace("kafka://", "")
    producer = AIOKafkaProducer(
        bootstrap_servers=broker,
        value_serializer=lambda v: json.dumps(v, default=str).encode(),
        key_serializer=lambda k: k.encode() if isinstance(k, str) else k,
    )
    await producer.start()
    return producer


async def _publish_event(
    producer: AIOKafkaProducer,
    event: ACHCreditEvent | ACHDebitEvent,
) -> None:
    await producer.send(
        TOPIC_ACH_RAW,
        key=event.sender_id,
        value=json.loads(event.model_dump_json()),
    )
    logger.info(
        "published %s | trace=%s | amount=$%.2f | batch=%s",
        event.ach_event_type,
        event.trace_number,
        event.amount_cents / 100,
        event.batch_id[:8],
    )


# ---------------------------------------------------------------------------
# Return scheduler
# ---------------------------------------------------------------------------

async def _schedule_return(
    producer: AIOKafkaProducer,
    original: ACHCreditEvent | ACHDebitEvent,
) -> None:
    delay = random.uniform(30, 90)    # seconds in dev; 1-3 business days in production
    await asyncio.sleep(delay)

    return_code, return_reason = random.choice(list(_RETURN_CODES.items()))
    return_event = _build_return_event(original, return_code, return_reason)
    await _publish_event(producer, return_event)
    logger.info(
        "returned trace=%s code=%s after %.1fs",
        original.trace_number, return_code, delay,
    )


# ---------------------------------------------------------------------------
# Batch builder
# ---------------------------------------------------------------------------

async def _fire_batch(producer: AIOKafkaProducer) -> None:
    """
    Produce one NACHA batch — 5-25 transactions sharing a batch_id
    and batch_submission_time. Publishes with 0.1s spacing between messages.
    5% of transactions schedule a return.
    """
    batch_id             = str(uuid.uuid4())
    batch_size           = random.randint(5, 25)
    batch_submission_time = datetime.now(timezone.utc)
    odfi                 = random.choice(_BANKS)

    logger.info("firing batch %s | size=%d | odfi=%s", batch_id[:8], batch_size, odfi["name"])

    for seq in range(1, batch_size + 1):
        sender   = random.choice(_ACCOUNTS)
        receiver = random.choice([a for a in _ACCOUNTS if a != sender])
        rdfi     = random.choice(_BANKS)
        amount   = _random_amount_cents()
        sec_code = _random_sec_code()
        trace    = _generate_trace_number(odfi["routing"], seq)

        # 70% credit, 30% debit
        ach_type = (
            ACHEventType.ACH_CREDIT if random.random() < 0.70
            else ACHEventType.ACH_DEBIT
        )

        event = _build_ach_event(
            ach_event_type=ach_type,
            sender=sender,
            receiver=receiver,
            odfi=odfi,
            rdfi=rdfi,
            batch_id=batch_id,
            batch_submission_time=batch_submission_time,
            trace_number=trace,
            amount_cents=amount,
            sec_code=sec_code,
        )

        await _publish_event(producer, event)

        # 5% return rate
        if random.random() < 0.05:
            asyncio.create_task(_schedule_return(producer, event))

        await asyncio.sleep(0.1)    # small spacing within batch


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run() -> None:
    """
    Fire ACH batches every 60-120 seconds.
    Runs until cancelled (Ctrl+C).
    """
    logger.info("starting ACH generator")
    producer = await _make_producer()

    try:
        while True:
            await _fire_batch(producer)
            wait = random.uniform(60, 120)
            logger.info("next batch in %.0fs", wait)
            await asyncio.sleep(wait)

    except asyncio.CancelledError:
        logger.info("ACH generator shutting down")
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(run())