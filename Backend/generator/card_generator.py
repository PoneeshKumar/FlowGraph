import asyncio
import json
import logging
from pyexpat import model
import random
from sqlite3.dbapi2 import Timestamp
import string
from datetime import datetime, timezone
from typing import Optional
from aiokafka import AIOKafkaProducer
from config import KAFKA_BROKER, TOPIC_CARD_RAW
from fx import to_usd_cents
from models.card_events import (
    CardAuthEvent,
    CardSettlementEvent,
    EventStatus,
    EventType,
    hash_pan,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# test with random # of cardholders to get their PAN #, in reality this is from card network
CARDHOLDERS = [
    {"name": f"Cardholder_{i:02d}", "pan": f"4{''.join(random.choices(string.digits, k=15))}"}
    for i in range(random.randint(0,60))
]

# fake merchants with realistic MCC codes
MERCHANTS = [
    {"merchant_id": "MCH_001", "name": "FreshMart Grocery",     "mcc": "5411"},
    {"merchant_id": "MCH_002", "name": "Shell Gas Station",      "mcc": "5541"},
    {"merchant_id": "MCH_003", "name": "The Burger Joint",       "mcc": "5812"},
    {"merchant_id": "MCH_004", "name": "Amazon.com",             "mcc": "5999"},
    {"merchant_id": "MCH_005", "name": "Walgreens Pharmacy",     "mcc": "5912"},
    {"merchant_id": "MCH_006", "name": "Delta Airlines",         "mcc": "4511"},
    {"merchant_id": "MCH_007", "name": "Hilton Hotels",          "mcc": "7011"},
    {"merchant_id": "MCH_008", "name": "Netflix",                "mcc": "7395"},
    {"merchant_id": "MCH_009", "name": "Home Depot",             "mcc": "5211"},
    {"merchant_id": "MCH_010", "name": "Uber Technologies",      "mcc": "4121"},
]

# making realistic amount distravutuons for test purposes
def random_amount_cents() -> int:
    roll = random.random()
    if roll < 0.7:
        return random.randint(500, 10_000)
    elif roll < 0.95:
        return random.randint(10_001, 50_000)
    else:
        return random.randint(50_001, 500_000)


# generate a random auth code
def random_auth_code() -> str:
    return "".join(string.ascii_uppercase + string.digits, k=6)

# generate a random terminal ID
def random_terminal_id() -> str:
    return "TRM" + "".join(random.choices(string.digits, k=8))

# generate an interval time that exponentially decreases
def interval_time(rate_per_minute: float) -> float:
    return random.expovariate(rate_per_minute / 60.0)

# penidng settlements in prod this lives in redis

pending_settlements: dict[str, dict] = {}

# mock the auth event
class MockEventBuilder(model):

    def build_auth(cardholder: dict, merchant: dict, amount_cents: int, approved: bool,) -> CardAuthEvent:
        pan_hash = hash_pan(cardholder["pan"])
        auth_code = random_auth_code()
        now = datetime.now(timezone.utc)
    
        return CardAuthEvent(
            rail="CARD",
            event_type=EventType.AUTH,
            sender_id=pan_hash,
            receiver_id=merchant["merchant_id"],
            amount_cents=amount_cents,
            amount_usd_cents=to_usd_cents(amount_cents, "USD"),
            currency="USD",
            timestamp_utc=now,
            authorization_code=auth_code,
            approved=approved,
            terminal_id=_random_terminal_id(),
            merchant_category_code=merchant["mcc"],
            merchant_name=merchant["name"],
            status=EventStatus.PENDING if approved else EventStatus.DECLINED,
            raw_payload={
                "pan_last4": cardholder["pan"][-4:],
                "merchant_id": merchant["merchant_id"],
                "source": "card_generator",
            },
        )
    
    # Settlement event that matches the auth
    def build_settlement(auth: CardAuthEvent) -> CardSettlementEvent:
        now = datetime.now(timezone.utc)
    
        # Settlement amount can differ slightly — simulate tip (0-20%) or exact
        tip_multiplier = random.choice([1.0, 1.0, 1.0, 1.05, 1.10, 1.15, 1.20])
        settlement_amount = int(auth.amount_cents * tip_multiplier)
    
        return CardSettlementEvent(
            rail="CARD",
            event_type=EventType.SETTLEMENT,
            sender_id=auth.sender_id,
            receiver_id=auth.receiver_id,
            amount_cents=auth.amount_cents,           # original auth amount
            amount_usd_cents=to_usd_cents(settlement_amount, "USD"),
            currency="USD",
            timestamp_utc=now,
            authorization_code=auth.authorization_code,
            settlement_amount_cents=settlement_amount,
            settled_at_utc=now,
            status=EventStatus.SETTLED,
            raw_payload={
                "authorization_code": auth.authorization_code,
                "original_amount_cents": auth.amount_cents,
                "source": "card_generator",
            },
        )
    
# Kafka Producerasync def _make_producer() -> AIOKafkaProducer:
    # Strip the faust-style "kafka://" prefix — aiokafka wants "host:port"
async def _make_producer() -> AIOKafkaProducer:
    broker = KAFKA_BROKER.replace("kafka://", "")
    producer = AIOKafkaProducer(
        bootstrap_servers=broker,
        value_serializer=lambda v: json.dumps(v).encode(),
        key_serializer=lambda k: k.encode() if isinstance(k, str) else k,
    )
    await producer.start()
    return producer
 
 
async def _publish_event(
    producer: AIOKafkaProducer,
    event: CardAuthEvent | CardSettlementEvent,
) -> None:
    await producer.send(
        TOPIC_CARD_RAW,
        key=event.sender_id,           # partition by sender — auth + settlement same partition
        value=json.loads(event.model_dump_json()),
    )
    logger.info(
        "published %s | auth_code=%s | amount=$%.2f | approved=%s",
        event.event_type,
        event.authorization_code,
        event.amount_cents / 100,
        getattr(event, "approved", "N/A"),
    )
 
 
# ---------------------------------------------------------------------------
# Settlement scheduler
# Fires settlements 5–30 seconds after their auth (simulates 1-3 day delay)
# ---------------------------------------------------------------------------
 
async def _schedule_settlement(
    producer: AIOKafkaProducer,
    auth: CardAuthEvent,
) -> None:
    delay = random.uniform(5, 30)   # seconds in dev; days in production
    await asyncio.sleep(delay)
    settlement = build_settlement(auth)
    await _publish_event(producer, settlement)
    pending_settlements.pop(auth.authorization_code, None)
    logger.info("settled auth_code=%s after %.1fs delay", auth.authorization_code, delay)
 
 
# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
 
async def run(rate_per_minute: float = 30.0) -> None:
    """
    Produce card auth events at `rate_per_minute` using Poisson arrival timing.
    10% of auths are declined — no settlement follows a declined auth.
 
    Runs until cancelled (Ctrl+C).
    """
    logger.info("starting card generator at ~%.0f events/min", rate_per_minute)
    producer = await _make_producer()
 
    try:
        while True:
            # Poisson inter-arrival wait
            interval = interval_time(rate_per_minute)
            await asyncio.sleep(interval)
 
            cardholder = random.choice(CARDHOLDERS)
            merchant   = random.choice(MERCHANTS)
            amount     = random_amount_cents()
            approved   = random.random() > 0.10   # 90% approval rate
 
            auth = build_auth(cardholder, merchant, amount, approved)
            await _publish_event(producer, auth)
 
            if approved:
                # Schedule settlement in background — doesn't block the auth loop
                pending_settlements[auth.authorization_code] = auth
                asyncio.create_task(_schedule_settlement(producer, auth))
 
    except asyncio.CancelledError:
        logger.info(
            "card generator shutting down — %d settlements still pending",
            len(pending_settlements),
        )
    finally:
        await producer.stop()
 
 
if __name__ == "__main__":
    asyncio.run(run())