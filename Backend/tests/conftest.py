"""Integration test fixtures — require Docker (testcontainers spins up real
Postgres / Neo4j / Redis).

UNVERIFIED IN CI YET: run `pip install -r requirements.txt` and ensure Docker is
running, then `pytest Backend/tests`. The testcontainers API is version-sensitive
(pinned testcontainers==4.7.2); adjust the connection-detail calls if you bump it.
"""
import os

import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer
from testcontainers.neo4j import Neo4jContainer
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session", autouse=True)
def _stores():
    with PostgresContainer("postgres:16") as pg, \
         Neo4jContainer("neo4j:5") as neo, \
         RedisContainer("redis:7-alpine") as rd:

        os.environ["POSTGRES_HOST"] = pg.get_container_host_ip()
        os.environ["POSTGRES_PORT"] = str(pg.get_exposed_port(5432))
        os.environ["POSTGRES_DB"] = pg.dbname
        os.environ["POSTGRES_USER"] = pg.username
        os.environ["POSTGRES_PASSWORD"] = pg.password

        os.environ["NEO4J_URI"] = neo.get_connection_url()
        os.environ["NEO4J_USER"] = "neo4j"
        os.environ["NEO4J_PASSWORD"] = neo.password

        os.environ["REDIS_HOST"] = rd.get_container_host_ip()
        os.environ["REDIS_PORT"] = str(rd.get_exposed_port(6379))

        yield


@pytest_asyncio.fixture(autouse=True)
async def _reset_clients():
    """Drop cached pools/drivers/clients and re-init schema before each test so
    module-level singletons pick up the container env and tables start clean."""
    from db import postgres, neo4j, redis as redis_db

    # reset cached singletons
    postgres._pool = None
    neo4j._driver = None
    redis_db._client = None

    await postgres.init_schema()
    await neo4j.init_constraints()

    # clean slate each test
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE payments")
    async with neo4j.get_driver().session() as s:
        await s.run("MATCH (n) DETACH DELETE n")
    await redis_db.get_client().flushall()

    yield


def make_event(event_id="evt-1", sender="alice", receiver="bob",
               amount_cents=10_000, currency="USD", rail="CARD"):
    return {
        "event_id": event_id,
        "rail": rail,
        "sender_id": sender,
        "receiver_id": receiver,
        "amount_cents": amount_cents,
        "amount_usd_cents": amount_cents,  # USD 1:1 for tests
        "currency": currency,
        "timestamp_utc": "2026-06-02T00:00:00+00:00",
        "status": "SETTLED",
        "raw_payload": {"src": "test"},
    }
