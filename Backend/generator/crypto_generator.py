"""
Backend/generator/crypto_generator.py

Produces fake CryptoEvent messages to payments.raw.crypto at 3-10 events/min.
60% Ethereum, 40% Bitcoin. Events start as PENDING then fire a confirmation
update after a chain-appropriate delay.

Partition key: sender_id (wallet address).

Run:
    python -m generator.crypto_generator
"""

import asyncio
import json
import logging
import random
import secrets
import string
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

from config import KAFKA_BROKER, TOPIC_CRYPTO_RAW
from fx import to_usd_cents
from models.crypto_events import (
    BTC_CONFIRMATIONS_REQUIRED,
    ETH_CONFIRMATIONS_REQUIRED,
    ChainType,
    CryptoEvent,
    CryptoTxStatus,
)
from models.card_events import EventStatus, EventType, Rail

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Fake wallet pools
# ---------------------------------------------------------------------------

# 20 fake Ethereum addresses — 0x + 40 hex chars
_ETH_WALLETS = [
    "0x" + secrets.token_hex(20)
    for _ in range(20)
]

# 20 fake Bitcoin addresses — legacy format (starts with 1)
_BTC_CHARS = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BTC_WALLETS = [
    "1" + "".join(random.choices(_BTC_CHARS, k=random.randint(25, 33)))
    for _ in range(20)
]

# Incrementing block heights — realistic starting values
_eth_block_height = 19_800_000
_btc_block_height = 840_000


# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

def _eth_amount_wei() -> int:
    """0.01 – 10 ETH in wei (1 ETH = 10^18 wei)."""
    eth = random.uniform(0.01, 10.0)
    return int(eth * 10**18)


def _btc_amount_satoshis() -> int:
    """0.001 – 2 BTC in satoshis (1 BTC = 100,000,000 satoshis)."""
    btc = random.uniform(0.001, 2.0)
    return int(btc * 100_000_000)


def _eth_gas_fee_gwei() -> int:
    """gas_units * gas_price_gwei — realistic range."""
    gas_units  = random.randint(21_000, 200_000)
    gas_price  = random.randint(10, 100)       # gwei
    return gas_units * gas_price


def _fake_block_hash() -> str:
    return "0x" + secrets.token_hex(32)


def _fake_txid() -> str:
    """Bitcoin txid — 64 hex chars, no 0x prefix."""
    return secrets.token_hex(32)


def _fake_tx_hash() -> str:
    """Ethereum tx hash — 0x + 64 hex chars."""
    return "0x" + secrets.token_hex(32)


def _poisson_interval(rate_per_minute: float) -> float:
    return random.expovariate(rate_per_minute / 60.0)


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------

def _build_eth_event(
    sender: str,
    receiver: str,
    crypto_status: CryptoTxStatus,
    confirmations: int,
    block_height: int | None,
    block_hash: str | None,
    tx_hash: str,
    amount_wei: int,
    gas_fee_gwei: int,
) -> CryptoEvent:
    now = datetime.now(timezone.utc)
    return CryptoEvent(
        rail=Rail.CRYPTO,
        event_type=EventType.SETTLEMENT,
        sender_id=sender,
        receiver_id=receiver,
        amount_cents=amount_wei,                         # wei stored in amount_cents
        amount_usd_cents=to_usd_cents(
            int(amount_wei / 10**18 * 100),              # rough ETH→cents
            "USD",
        ),
        currency="ETH",
        timestamp_utc=now,
        chain_type=ChainType.ETHEREUM,
        crypto_status=crypto_status,
        confirmations=confirmations,
        block_height=block_height,
        block_hash=block_hash,
        tx_hash=tx_hash,
        gas_fee_gwei=gas_fee_gwei,
        raw_payload={
            "chain": "ETHEREUM",
            "tx_hash": tx_hash,
            "source": "crypto_generator",
        },
    )


def _build_btc_event(
    sender: str,
    receiver: str,
    crypto_status: CryptoTxStatus,
    confirmations: int,
    block_height: int | None,
    block_hash: str | None,
    txid: str,
    amount_satoshis: int,
) -> CryptoEvent:
    now = datetime.now(timezone.utc)
    return CryptoEvent(
        rail=Rail.CRYPTO,
        event_type=EventType.SETTLEMENT,
        sender_id=sender,
        receiver_id=receiver,
        amount_cents=amount_satoshis,                    # satoshis stored in amount_cents
        amount_usd_cents=to_usd_cents(
            int(amount_satoshis / 100_000_000 * 100),    # rough BTC→cents
            "USD",
        ),
        currency="BTC",
        timestamp_utc=now,
        chain_type=ChainType.BITCOIN,
        crypto_status=crypto_status,
        confirmations=confirmations,
        block_height=block_height,
        block_hash=block_hash,
        txid=txid,
        total_input_count=random.randint(1, 5),
        total_output_count=random.randint(1, 3),
        raw_payload={
            "chain": "BITCOIN",
            "txid": txid,
            "source": "crypto_generator",
        },
    )


# ---------------------------------------------------------------------------
# Confirmation scheduler
# Fires a CONFIRMED update after chain-appropriate delay
# ---------------------------------------------------------------------------

async def _confirm_eth(
    producer: AIOKafkaProducer,
    sender: str,
    receiver: str,
    tx_hash: str,
    amount_wei: int,
    gas_fee_gwei: int,
) -> None:
    global _eth_block_height
    delay = random.uniform(15, 45)    # seconds in dev; ~3 min on mainnet
    await asyncio.sleep(delay)
    _eth_block_height += random.randint(1, 3)
    event = _build_eth_event(
        sender=sender,
        receiver=receiver,
        crypto_status=CryptoTxStatus.CONFIRMED,
        confirmations=ETH_CONFIRMATIONS_REQUIRED,
        block_height=_eth_block_height,
        block_hash=_fake_block_hash(),
        tx_hash=tx_hash,
        amount_wei=amount_wei,
        gas_fee_gwei=gas_fee_gwei,
    )
    await _publish_event(producer, event)
    logger.info("ETH confirmed tx_hash=%s after %.1fs", tx_hash[:12], delay)


async def _confirm_btc(
    producer: AIOKafkaProducer,
    sender: str,
    receiver: str,
    txid: str,
    amount_satoshis: int,
) -> None:
    global _btc_block_height
    delay = random.uniform(30, 90)    # seconds in dev; ~60 min on mainnet
    await asyncio.sleep(delay)
    _btc_block_height += 1
    event = _build_btc_event(
        sender=sender,
        receiver=receiver,
        crypto_status=CryptoTxStatus.CONFIRMED,
        confirmations=BTC_CONFIRMATIONS_REQUIRED,
        block_height=_btc_block_height,
        block_hash=_fake_block_hash(),
        txid=txid,
        amount_satoshis=amount_satoshis,
    )
    await _publish_event(producer, event)
    logger.info("BTC confirmed txid=%s after %.1fs", txid[:12], delay)


# ---------------------------------------------------------------------------
# Kafka producer
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


async def _publish_event(producer: AIOKafkaProducer, event: CryptoEvent) -> None:
    await producer.send(
        TOPIC_CRYPTO_RAW,
        key=event.sender_id,
        value=json.loads(event.model_dump_json()),
    )
    logger.info(
        "published %s | chain=%s | status=%s | amount_native=%.6f",
        event.event_type,
        event.chain_type,
        event.crypto_status,
        event.amount_native,
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run(rate_per_minute: float = 6.0) -> None:
    """
    Produce crypto events at ~rate_per_minute using Poisson arrival timing.
    60% ETH, 40% BTC. Each PENDING event spawns a background confirmation task.

    Runs until cancelled (Ctrl+C).
    """
    global _eth_block_height, _btc_block_height
    logger.info("starting crypto generator at ~%.0f events/min", rate_per_minute)
    producer = await _make_producer()

    try:
        while True:
            interval = _poisson_interval(rate_per_minute)
            await asyncio.sleep(interval)

            is_eth = random.random() < 0.60

            if is_eth:
                sender   = random.choice(_ETH_WALLETS)
                receiver = random.choice([w for w in _ETH_WALLETS if w != sender])
                tx_hash  = _fake_tx_hash()
                amount   = _eth_amount_wei()
                gas      = _eth_gas_fee_gwei()

                # Publish PENDING first
                event = _build_eth_event(
                    sender=sender,
                    receiver=receiver,
                    crypto_status=CryptoTxStatus.PENDING,
                    confirmations=0,
                    block_height=None,
                    block_hash=None,
                    tx_hash=tx_hash,
                    amount_wei=amount,
                    gas_fee_gwei=gas,
                )
                await _publish_event(producer, event)

                # Schedule confirmation in background
                asyncio.create_task(
                    _confirm_eth(producer, sender, receiver, tx_hash, amount, gas)
                )

            else:
                sender   = random.choice(_BTC_WALLETS)
                receiver = random.choice([w for w in _BTC_WALLETS if w != sender])
                txid     = _fake_txid()
                amount   = _btc_amount_satoshis()

                # Publish PENDING first
                event = _build_btc_event(
                    sender=sender,
                    receiver=receiver,
                    crypto_status=CryptoTxStatus.PENDING,
                    confirmations=0,
                    block_height=None,
                    block_hash=None,
                    txid=txid,
                    amount_satoshis=amount,
                )
                await _publish_event(producer, event)

                # Schedule confirmation in background
                asyncio.create_task(
                    _confirm_btc(producer, sender, receiver, txid, amount)
                )

    except asyncio.CancelledError:
        logger.info("crypto generator shutting down")
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(run())