import os
from typing import Any, Optional

from neo4j import AsyncDriver, AsyncGraphDatabase

# Neo4j holds the derived graph: Account nodes with cumulative USD volume, and
# SENT_TO edges (one per rail) carrying total USD + tx_count.
#
# Idempotency against Kafka at-least-once delivery AND outbox replay:
#   1. The :Event(id) UNIQUE constraint guarantees concurrent MERGE of the same
#      event_id can't create two Event nodes.
#   2. The whole mutation is ONE transaction (single Cypher statement), so the
#      Event-node create and the edge increment are atomic — no lost weight.
#   3. The increment is applied only when the Event node was newly created this
#      transaction (isNew), so reprocessing the same event never double-counts.
# Deadlocks from concurrent node updates (an account is sender on one partition
# and receiver on another) are retried automatically by the managed-transaction
# execute_write, which backs off and retries TransientError.

_driver: Optional[AsyncDriver] = None

_CONSTRAINT = "CREATE CONSTRAINT event_id_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.id IS UNIQUE"

_UPSERT = """
MERGE (e:Event {id: $event_id})
ON CREATE SET e._new = true
WITH e, coalesce(e._new, false) AS isNew
SET e._new = null

MERGE (s:Account {id: $sender_id})
  ON CREATE SET s.first_seen = $ts, s.last_seen = $ts, s.cumulative_volume_usd = 0
MERGE (r:Account {id: $receiver_id})
  ON CREATE SET r.first_seen = $ts, r.last_seen = $ts, r.cumulative_volume_usd = 0
MERGE (s)-[edge:SENT_TO {rail: $rail}]->(r)
  ON CREATE SET edge.total_amount_usd = 0, edge.tx_count = 0,
                edge.first_seen = $ts, edge.last_seen = $ts

// fold the amount in exactly once — only on the transaction that created :Event
FOREACH (_ IN CASE WHEN isNew THEN [1] ELSE [] END |
  SET s.last_seen = $ts,
      s.cumulative_volume_usd = s.cumulative_volume_usd + $amount_usd,
      r.last_seen = $ts,
      r.cumulative_volume_usd = r.cumulative_volume_usd + $amount_usd,
      edge.total_amount_usd = edge.total_amount_usd + $amount_usd,
      edge.tx_count = edge.tx_count + 1,
      edge.last_seen = $ts
)
"""


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(
                os.getenv("NEO4J_USER", "neo4j"),
                os.getenv("NEO4J_PASSWORD", "changeme"),
            ),
        )
    return _driver


async def init_constraints() -> None:
    async with get_driver().session() as session:
        await session.run(_CONSTRAINT)


async def upsert_payment_graph(event: dict[str, Any]) -> None:
    params = {
        "event_id": str(event["event_id"]),
        "sender_id": event["sender_id"],
        "receiver_id": event["receiver_id"],
        "rail": str(event["rail"]),
        "amount_usd": event["amount_usd_cents"],
        "ts": str(event["timestamp_utc"]),
    }

    async def _work(tx):
        await tx.run(_UPSERT, **params)

    # execute_write retries TransientError (incl. deadlocks) with backoff
    async with get_driver().session() as session:
        await session.execute_write(_work)
