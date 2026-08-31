# backend/app/db/neo4j.py
from neo4j import AsyncGraphDatabase, AsyncDriver
from app.core.config import settings

class Neo4jSessionManager:
    def __init__(self):
        self.driver: AsyncDriver | None = None

    def connect(self):
        self.driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )

    async def close(self):
        if self.driver:
            await self.driver.close()

neo4j_client = Neo4jSessionManager()