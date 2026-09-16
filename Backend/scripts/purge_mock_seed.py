"""Remove the demo accounts written by scripts/seed_mock_data.py.

The 13 `acc_*` accounts carry 2026 timestamps and a hand-set risk_score, so they
pollute dataset-anchored stats and the snapshot. Deletes their nodes (with all
relationships) and their `edge:acc_*` Redis keys. Safe to re-run.

    python3 scripts/purge_mock_seed.py
"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import NEO4J_DATABASE
from db.neo4j import Neo4jClient
from db.redis import RedisClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("purge_mock_seed")

DELETE_Q = (
    "MATCH (a:Account) WHERE a.id STARTS WITH 'acc_' "
    "WITH a LIMIT 1000 DETACH DELETE a RETURN count(*) AS n"
)


async def purge() -> int:
    neo4j = Neo4jClient()
    await neo4j.initialize()
    deleted = 0
    try:
        async with neo4j.driver.session(database=NEO4J_DATABASE) as s:
            while True:
                rec = await (await s.run(DELETE_Q)).single()
                n = int(rec["n"]) if rec else 0
                deleted += n
                if n == 0:
                    break
    finally:
        await neo4j.close()
    log.info("deleted %d mock accounts", deleted)

    redis = RedisClient()
    await redis.initialize()
    removed = 0
    try:
        async for key in redis.client.scan_iter(match="edge:acc_*", count=1000):
            await redis.client.delete(key)
            removed += 1
    finally:
        await redis.close()
    log.info("removed %d mock redis keys", removed)
    return deleted


if __name__ == "__main__":
    asyncio.run(purge())
