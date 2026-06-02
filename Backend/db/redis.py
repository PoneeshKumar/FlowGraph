import os
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis

# Redis holds the fast time-window cache for each edge:
#   edge:{s}:{r}             ZSET, score=unix ts, member=event_id
#                            -> ZRANGEBYSCORE gives events in a time window in us
#   edge:{s}:{r}:by_currency HASH, field=currency, value=running native total
#
# Idempotency: ZADD NX + HINCRBY must be atomic, because HINCRBY on its own is
# NOT idempotent — a redelivery or outbox replay would double-count it. The Lua
# script runs HINCRBY only when ZADD actually added a new member (returns 1), so
# the per-currency total moves exactly once per event. Member is event_id alone
# (not event_id:amount) so a re-stubbed FX amount on replay can't create a second
# member and defeat the dedup.

_client: Optional[aioredis.Redis] = None

_ZADD_NX_THEN_HINCRBY = """
local added = redis.call('ZADD', KEYS[1], 'NX', ARGV[1], ARGV[2])
if added == 1 then
  redis.call('HINCRBY', KEYS[2], ARGV[3], ARGV[4])
end
return added
"""


def get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            decode_responses=True,
        )
    return _client


def _score(raw: Any) -> float:
    if isinstance(raw, datetime):
        ts = raw
    else:
        ts = datetime.fromisoformat(str(raw))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


async def zadd_edge(event: dict[str, Any]) -> None:
    s = event["sender_id"]
    r = event["receiver_id"]
    await get_client().eval(
        _ZADD_NX_THEN_HINCRBY,
        2,                                # numkeys
        f"edge:{s}:{r}",                  # KEYS[1]
        f"edge:{s}:{r}:by_currency",      # KEYS[2]
        _score(event["timestamp_utc"]),   # ARGV[1] score
        str(event["event_id"]),           # ARGV[2] member
        event["currency"],                # ARGV[3] hash field
        event["amount_cents"],            # ARGV[4] native increment
    )
