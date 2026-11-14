"""The outbox pattern's core guarantee: a payment and its outbox row commit together.

If they don't, a crash between the two writes leaves a payment in Postgres that the
sync worker never sees — it is in the ledger but never reaches the graph, silently,
forever. These run against the dev container; they skip when it isn't reachable.

    POSTGRES_DSN='postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph' \
        python3 -m pytest tests/test_outbox_atomicity.py -v
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# --- no database needed: the consumer's wiring ------------------------------

def test_consumer_writes_both_rows_on_one_connection():
    """The regression guard that runs in CI. `_write_to_postgres` must open one
    transaction and hand that same connection to both writes; passing neither (or
    two different connections) is the bug this file exists for."""
    from consumer import faust_app

    sentinel = object()
    entered = []

    @asynccontextmanager
    async def fake_transaction():
        entered.append(True)
        yield sentinel

    pg = MagicMock(transaction=fake_transaction,
                   save_transaction=AsyncMock(), insert_outbox=AsyncMock())
    event = MagicMock(
        event_id=uuid4(), timestamp_utc=datetime(2022, 9, 1, tzinfo=timezone.utc),
        sender_id="s", receiver_id="r", amount_cents=100, currency="USD",
        schema_version=1, authorization_code=None)
    event.rail.value = "CARD"
    event.event_type.value = "SETTLEMENT"
    event.status.value = "SETTLED"

    with patch.object(faust_app, "postgres_client", pg):
        asyncio.run(faust_app._write_to_postgres(event, {"raw": True}))

    assert entered == [True], "no transaction was opened"
    assert pg.save_transaction.await_args.kwargs["conn"] is sentinel
    assert pg.insert_outbox.await_args.kwargs["conn"] is sentinel


# --- the rest need the dev container ----------------------------------------


def _run(coro_fn):
    async def wrapper():
        from db.postgres import PostgresClient
        pg = PostgresClient()
        try:
            # Bounded: a *paused* container leaves the port bound but unanswered, so
            # connecting hangs rather than being refused — without this the skip path
            # costs a connect timeout per test (four minutes for this file).
            await asyncio.wait_for(pg.initialize(), timeout=5)
            await pg.ensure_transaction_tables()
        except (Exception, asyncio.TimeoutError) as exc:  # noqa: BLE001
            pytest.skip(f"Postgres not reachable: {type(exc).__name__}: {exc}")
        try:
            return await coro_fn(pg)
        finally:
            await pg.close()
    return asyncio.run(wrapper())


async def _save(pg, txid, conn):
    await pg.save_transaction(
        transaction_id=txid, rail="CARD", event_type="SETTLEMENT", status="SETTLED",
        sender_id="atomicity_sender", receiver_id="atomicity_receiver",
        amount_cents=1234, currency="USD",
        timestamp_utc=datetime.now(timezone.utc).replace(tzinfo=None),
        raw_payload={"test": "outbox atomicity"}, conn=conn)


async def _cleanup(pg, txid):
    async with pg._get_connection() as conn:
        await conn.execute("DELETE FROM outbox WHERE transaction_id = $1::uuid", txid)
        await conn.execute("DELETE FROM transactions WHERE id = $1::uuid", txid)


def test_both_rows_commit_together():
    async def fn(pg):
        txid = str(uuid4())
        try:
            async with pg.transaction() as conn:
                await _save(pg, txid, conn)
                await pg.insert_outbox(transaction_id=txid, idempotency_key=f"atomic:{txid}",
                                       event_payload={"event_id": txid}, conn=conn)
            txn = await pg.get_transaction(txid)
            async with pg._get_connection() as c:
                outbox = await c.fetchrow("SELECT * FROM outbox WHERE transaction_id = $1::uuid", txid)
            return txn, outbox
        finally:
            await _cleanup(pg, txid)
    txn, outbox = _run(fn)
    assert txn is not None and outbox is not None
    assert outbox["status"] == "pending"


def test_a_failed_outbox_write_rolls_back_the_payment():
    """The regression this file exists for. Before the fix the two writes used
    separate pooled connections, so the payment row survived on its own."""
    async def fn(pg):
        txid = str(uuid4())
        try:
            with pytest.raises(RuntimeError):
                async with pg.transaction() as conn:
                    await _save(pg, txid, conn)
                    raise RuntimeError("simulated crash before the outbox row lands")
            return await pg.get_transaction(txid)
        finally:
            await _cleanup(pg, txid)
    assert _run(fn) is None, "payment row survived a rolled-back transaction"


def test_outbox_failure_rolls_back_too():
    """A real failure mode rather than a raised exception: the outbox row violates
    its foreign key, which must take the payment row down with it."""
    async def fn(pg):
        txid = str(uuid4())
        orphan = str(uuid4())
        try:
            with pytest.raises(Exception):
                async with pg.transaction() as conn:
                    await _save(pg, txid, conn)
                    # transaction_id has an FK to transactions(id); this one does not exist
                    await pg.insert_outbox(transaction_id=orphan, idempotency_key=f"atomic:{orphan}",
                                           event_payload={"event_id": orphan}, conn=conn)
            return await pg.get_transaction(txid)
        finally:
            await _cleanup(pg, txid)
    assert _run(fn) is None, "payment row survived an outbox constraint violation"


def test_redelivery_is_a_no_op():
    """Kafka can redeliver. Both writes are idempotent, so replaying the same event
    must not raise or duplicate."""
    async def fn(pg):
        txid = str(uuid4())
        key = f"atomic:{txid}"
        try:
            for _ in range(2):
                async with pg.transaction() as conn:
                    await _save(pg, txid, conn)
                    await pg.insert_outbox(transaction_id=txid, idempotency_key=key,
                                           event_payload={"event_id": txid}, conn=conn)
            async with pg._get_connection() as c:
                return await c.fetchval("SELECT count(*) FROM outbox WHERE transaction_id = $1::uuid", txid)
        finally:
            await _cleanup(pg, txid)
    assert _run(fn) == 1


def test_standalone_writes_still_work_without_a_transaction():
    """The bulk ingest calls save_transaction with no outbox row and no conn."""
    async def fn(pg):
        txid = str(uuid4())
        try:
            await _save(pg, txid, None)
            return await pg.get_transaction(txid)
        finally:
            await _cleanup(pg, txid)
    assert _run(fn) is not None
