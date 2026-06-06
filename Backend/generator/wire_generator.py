import asyncio
import json
import logging
import random
from datetime import datetime, date, timedelta, timezone

from aiokafka import AIOKafkaProducer

from config import KAFKA_BROKER, TOPIC_WIRE_RAW
from fx import to_usd_cents
from models.wire_events import (
    CorrespondentHop,
    WireEvent,
    WireEventStatus,
    WireType,
    generate_imad,
    generate_uetr,
    hash_account_number,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Static pools
# ---------------------------------------------------------------------------

_SENDER_ACCOUNTS = [f"ACC_SND_{i:03d}" for i in range(1, 16)]
_RECEIVER_ACCOUNTS = [f"ACC_RCV_{i:03d}" for i in range(1, 16)]

_US_BANKS = [
    {"bank_id": "JPMC", "name": "JPMorgan Chase",   "routing": "021000021", "bic": "CHASUS33"},
    {"bank_id": "BOFA", "name": "Bank of America",  "routing": "026009593", "bic": "BOFAUS3N"},
    {"bank_id": "WFAR", "name": "Wells Fargo",       "routing": "121000248", "bic": "WFBIUS6S"},
    {"bank_id": "CITI", "name": "Citibank",          "routing": "021000089", "bic": "CITIUS33"},
    {"bank_id": "USBC", "name": "US Bank",           "routing": "091000022", "bic": "USBKUS44"},
    {"bank_id": "PNCB", "name": "PNC Bank",          "routing": "043000096", "bic": "PNCCUS33"},
    {"bank_id": "GSNY", "name": "Goldman Sachs",     "routing": "026015079", "bic": "GSNYUS33"},
    {"bank_id": "HSBU", "name": "HSBC USA",          "routing": "021001088", "bic": "MRMDUS33"},
]

# Pool of 6 international correspondent banks used for SWIFT hop chains
_INTL_BANKS = [
    CorrespondentHop(bank_id="DEUTDEDB", bank_name="Deutsche Bank",           country="DE", role="INTERMEDIARY"),
    CorrespondentHop(bank_id="BNPAFRPP", bank_name="BNP Paribas",             country="FR", role="INTERMEDIARY"),
    CorrespondentHop(bank_id="HBUKGB4B", bank_name="HSBC London",             country="GB", role="INTERMEDIARY"),
    CorrespondentHop(bank_id="SCBLSGSG", bank_name="Standard Chartered",      country="SG", role="INTERMEDIARY"),
    CorrespondentHop(bank_id="MHCBJPJT", bank_name="Mizuho Bank",             country="JP", role="INTERMEDIARY"),
    CorrespondentHop(bank_id="UBSWCHZH", bank_name="UBS AG",                  country="CH", role="INTERMEDIARY"),
]

_PURPOSE_WEIGHTS = [
    ("TRADE",       0.40),
    ("INVESTMENT",  0.25),
    ("REAL_ESTATE", 0.20),
    ("OTHER",       0.15),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_amount_cents() -> int:
    roll = random.random()
    if roll < 0.70:
        return random.randint(1_000_000, 10_000_000)     # $10k – $100k
    elif roll < 0.95:
        return random.randint(10_000_001, 100_000_000)   # $100k – $1M
    else:
        return random.randint(100_000_001, 1_000_000_000)  # $1M – $10M


def _random_purpose() -> str:
    purposes, weights = zip(*_PURPOSE_WEIGHTS)
    return random.choices(purposes, weights=weights, k=1)[0]


def _poisson_interval(rate_per_minute: float) -> float:
    return random.expovariate(rate_per_minute / 60.0)


def _build_fedwire_event(
    sender_id: str,
    receiver_id: str,
    sender_bank: dict,
    receiver_bank: dict,
    amount_cents: int,
) -> WireEvent:
    now = datetime.now(timezone.utc)
    return WireEvent(
        wire_type=WireType.FEDWIRE,
        status=WireEventStatus.SETTLED,
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_bank_id=sender_bank["bic"],
        receiver_bank_id=receiver_bank["bic"],
        amount_cents=amount_cents,
        amount_usd_cents=to_usd_cents(amount_cents, "USD"),
        currency="USD",
        imad=generate_imad(),
        uetr=None,
        correspondent_hops=[],
        purpose_code=_random_purpose(),
        timestamp_utc=now,
        expected_settlement_date=now.date(),  # same-day settlement
        raw_payload={
            "sender_routing": sender_bank["routing"],
            "receiver_routing": receiver_bank["routing"],
            "source": "wire_generator",
        },
    )


def _build_swift_event(
    sender_id: str,
    receiver_id: str,
    sender_bank: dict,
    receiver_bank: dict,
    amount_cents: int,
    hops: list[CorrespondentHop],
    settlement_days: int,
) -> WireEvent:
    now = datetime.now(timezone.utc)
    return WireEvent(
        wire_type=WireType.SWIFT,
        status=WireEventStatus.PENDING,
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_bank_id=sender_bank["bic"],
        receiver_bank_id=receiver_bank["bic"],
        amount_cents=amount_cents,
        amount_usd_cents=to_usd_cents(amount_cents, "USD"),
        currency="USD",
        imad=None,
        uetr=generate_uetr(),
        correspondent_hops=hops,
        purpose_code=_random_purpose(),
        timestamp_utc=now,
        expected_settlement_date=(now + timedelta(days=settlement_days)).date(),
        raw_payload={
            "sender_bic": sender_bank["bic"],
            "receiver_bic": receiver_bank["bic"],
            "hop_count": len(hops),
            "source": "wire_generator",
        },
    )


def _build_swift_settlement(original: WireEvent) -> WireEvent:
    now = datetime.now(timezone.utc)
    return WireEvent(
        wire_type=WireType.SWIFT,
        status=WireEventStatus.SETTLED,
        sender_id=original.sender_id,
        receiver_id=original.receiver_id,
        sender_bank_id=original.sender_bank_id,
        receiver_bank_id=original.receiver_bank_id,
        amount_cents=original.amount_cents,
        amount_usd_cents=original.amount_usd_cents,
        currency=original.currency,
        uetr=original.uetr,
        correspondent_hops=original.correspondent_hops,
        purpose_code=original.purpose_code,
        timestamp_utc=now,
        expected_settlement_date=original.expected_settlement_date,
        raw_payload={
            **original.raw_payload,
            "settled_event_id": original.event_id,
            "source": "wire_generator",
        },
    )


# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------

async def _make_producer() -> AIOKafkaProducer:
    broker = KAFKA_BROKER.replace("kafka://", "")
    producer = AIOKafkaProducer(
        bootstrap_servers=broker,
        value_serializer=lambda v: json.dumps(v).encode(),
        key_serializer=lambda k: k.encode() if isinstance(k, str) else k,
    )
    await producer.start()
    return producer


async def _publish(producer: AIOKafkaProducer, event: WireEvent) -> None:
    await producer.send(
        TOPIC_WIRE_RAW,
        key=event.sender_id,
        value=json.loads(event.model_dump_json()),
    )
    logger.info(
        "published %s %s | uetr/imad=%s | amount=$%.2f | status=%s",
        event.wire_type,
        event.event_id[:8],
        (event.uetr or event.imad or "")[:12],
        event.amount_cents / 100,
        event.status,
    )


# ---------------------------------------------------------------------------
# Background task: SWIFT settlement after simulated delay
# ---------------------------------------------------------------------------

async def _schedule_swift_settlement(
    producer: AIOKafkaProducer,
    pending: WireEvent,
    delay_seconds: float,
) -> None:
    await asyncio.sleep(delay_seconds)
    settlement = _build_swift_settlement(pending)
    await _publish(producer, settlement)
    logger.info(
        "SWIFT settled uetr=%s after %.1fs",
        pending.uetr,
        delay_seconds,
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run(rate_per_minute: float = 10.0) -> None:
    """
    Produce WireEvent messages at ~rate_per_minute using Poisson arrival timing.
    50/50 FEDWIRE vs SWIFT split.  SWIFT wires are initially PENDING and settle
    after a simulated 10-60 second delay (represents 1-3 days in production).

    Runs until cancelled (Ctrl+C).
    """
    logger.info("starting wire generator at ~%.0f events/min", rate_per_minute)
    producer = await _make_producer()
    pending_swift: dict[str, WireEvent] = {}

    try:
        while True:
            interval = _poisson_interval(rate_per_minute)
            await asyncio.sleep(interval)

            sender_acct   = random.choice(_SENDER_ACCOUNTS)
            receiver_acct = random.choice(_RECEIVER_ACCOUNTS)
            sender_bank, receiver_bank = random.sample(_US_BANKS, 2)

            sender_id   = hash_account_number(sender_acct)
            receiver_id = hash_account_number(receiver_acct)
            amount      = _random_amount_cents()

            if random.random() < 0.5:
                # --- FEDWIRE: settled immediately ---
                event = _build_fedwire_event(
                    sender_id, receiver_id, sender_bank, receiver_bank, amount
                )
                await _publish(producer, event)
            else:
                # --- SWIFT: pending → settled after delay ---
                hop_count       = random.randint(1, 3)
                hops            = random.sample(_INTL_BANKS, hop_count)
                settlement_days = random.randint(1, 3)
                delay_seconds   = random.uniform(10, 60)

                event = _build_swift_event(
                    sender_id, receiver_id, sender_bank, receiver_bank,
                    amount, hops, settlement_days,
                )
                await _publish(producer, event)
                pending_swift[event.uetr] = event
                asyncio.create_task(
                    _schedule_swift_settlement(producer, event, delay_seconds)
                )

    except asyncio.CancelledError:
        logger.info(
            "wire generator shutting down — %d SWIFT settlements still pending",
            len(pending_swift),
        )
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(run())
