"""Idempotency + outbox integration tests for the consumer engine.

Maps to the test plan (T3/T5/T7/T9/T10/T11). These assert the exactly-once
*effects* that the design hinges on: redelivery and outbox replay must never
double-count graph weights or per-currency totals.

UNVERIFIED IN CI YET — needs Docker + `pip install -r requirements.txt`.
"""
import asyncio

import pytest

from db import postgres, neo4j, redis as redis_db
from tests.conftest import make_event

pytestmark = pytest.mark.asyncio


async def _edge_total_usd(sender, receiver, rail="CARD"):
    async with neo4j.get_driver().session() as s:
        rec = await (await s.run(
            "MATCH (:Account {id:$s})-[e:SENT_TO {rail:$rail}]->(:Account {id:$r}) "
            "RETURN e.total_amount_usd AS t, e.tx_count AS c",
            s=sender, r=receiver, rail=rail,
        )).single()
        return (rec["t"], rec["c"]) if rec else (None, None)


# T3 — redelivery dedup: same event written N times => 1 Postgres row, graph once
async def test_redelivery_dedup():
    ev = make_event()

    first = await postgres.write_payment(ev)
    assert first is not None
    await neo4j.upsert_payment_graph(first)
    await redis_db.zadd_edge(first)

    # redeliveries: write_payment returns None (ON CONFLICT) -> caller skips
    for _ in range(4):
        assert await postgres.write_payment(ev) is None

    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM payments")
    assert count == 1


# T5 — Neo4j replay safety: driving the graph twice for the same event_id
# increments the edge weight exactly once (Event-node guard + UNIQUE constraint)
async def test_neo4j_replay_exactly_once():
    ev = make_event(amount_cents=25_000)
    await neo4j.upsert_payment_graph(ev)
    await neo4j.upsert_payment_graph(ev)  # replay

    total, count = await _edge_total_usd("alice", "bob")
    assert total == 25_000
    assert count == 1


# T7 — Redis replay safety: ZADD NX + conditional HINCRBY applies currency once
async def test_redis_replay_exactly_once():
    ev = make_event(amount_cents=7_000, currency="USD")
    await redis_db.zadd_edge(ev)
    await redis_db.zadd_edge(ev)  # replay

    client = redis_db.get_client()
    members = await client.zcard("edge:alice:bob")
    by_cur = await client.hget("edge:alice:bob:by_currency", "USD")
    assert members == 1
    assert int(by_cur) == 7_000


# T9 — outbox not cleared while pending; T10/T11 — claim window
async def test_outbox_claim_window():
    ev = make_event(event_id="evt-stuck")
    row = await postgres.write_payment(ev)
    assert row is not None  # pending_graph_sync defaults TRUE

    # fresh row is NOT claimed (younger than the window)
    assert await postgres.claim_stuck_rows(older_than_seconds=60) == []

    # backdate it past the window, then it IS claimed
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE payments SET created_at = NOW() - interval '120 seconds' "
            "WHERE event_id = $1", "evt-stuck",
        )
    claimed = await postgres.claim_stuck_rows(older_than_seconds=60)
    assert [r["event_id"] for r in claimed] == ["evt-stuck"]

    # re-driving converges and clears the flag
    await neo4j.upsert_payment_graph(claimed[0])
    await redis_db.zadd_edge(claimed[0])
    await postgres.clear_outbox(claimed[0]["event_id"])
    assert await postgres.claim_stuck_rows(older_than_seconds=60) == []


# Combined: redelivery through the graph+cache twice never double-counts
async def test_full_replay_no_double_count():
    ev = make_event(event_id="evt-full", amount_cents=12_000)
    for _ in range(3):
        await neo4j.upsert_payment_graph(ev)
        await redis_db.zadd_edge(ev)

    total, count = await _edge_total_usd("alice", "bob")
    assert total == 12_000 and count == 1
    assert int(await redis_db.get_client().hget("edge:alice:bob:by_currency", "USD")) == 12_000
