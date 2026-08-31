"""Shared clients for the viz layer.

The read endpoints reuse the app's lightweight Neo4j session (app.db.neo4j), but
the PipelineRunner and the marked/run endpoints need the *full* db.neo4j.Neo4jClient
(algorithm methods) and db.postgres.PostgresClient. Those are initialized once at
app startup and held here.
"""
from typing import Optional

from db.neo4j import Neo4jClient
from db.postgres import PostgresClient

_neo4j: Optional[Neo4jClient] = None
_pg: Optional[PostgresClient] = None


async def startup() -> None:
    global _neo4j, _pg
    _neo4j = Neo4jClient()
    await _neo4j.initialize()
    _pg = PostgresClient()
    await _pg.initialize()


async def shutdown() -> None:
    global _neo4j, _pg
    if _neo4j is not None:
        await _neo4j.close()
    if _pg is not None:
        await _pg.close()
    _neo4j = None
    _pg = None


def neo4j() -> Neo4jClient:
    return _neo4j


def pg() -> PostgresClient:
    return _pg
