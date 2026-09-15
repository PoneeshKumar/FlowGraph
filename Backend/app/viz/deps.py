"""Shared clients for the viz layer.

The read endpoints reuse the app's lightweight Neo4j session (app.db.neo4j), but
the PipelineRunner and the marked/run endpoints need the *full* db.neo4j.Neo4jClient
(algorithm methods) and db.postgres.PostgresClient. Those are initialized once at
app startup and held here. The dataset upload job also needs the full
db.redis.RedisClient (bulk time-series writes during ingest).
"""
from typing import Optional

from db.neo4j import Neo4jClient
from db.postgres import PostgresClient
from db.redis import RedisClient

_neo4j: Optional[Neo4jClient] = None
_pg: Optional[PostgresClient] = None
_redis: Optional[RedisClient] = None


async def startup() -> None:
    global _neo4j, _pg, _redis
    _neo4j = Neo4jClient()
    await _neo4j.initialize()
    await _neo4j.init_constraints()   # idempotent; adds the ts/community indexes
    _pg = PostgresClient()
    await _pg.initialize()
    await _pg.ensure_app_meta_table()   # migration 004, idempotent
    _redis = RedisClient()
    await _redis.initialize()
    from app.viz import truth
    truth.preload()      # parse ground-truth labels once, off the request path


async def shutdown() -> None:
    global _neo4j, _pg, _redis
    if _neo4j is not None:
        await _neo4j.close()
    if _pg is not None:
        await _pg.close()
    if _redis is not None:
        await _redis.close()
    _neo4j = None
    _pg = None
    _redis = None


def neo4j() -> Neo4jClient:
    return _neo4j


def pg() -> PostgresClient:
    return _pg


def redis() -> RedisClient:
    return _redis
