from typing import List

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "FlowGraph Intelligence Engine"
    API_V1_STR: str = "/api"

    # CORS — explicit dev origins (Vite dev server, CRA). Override in prod.
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]
    
    # Storage & Drivers
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changeme"
    
    REDIS_URL: str = "redis://localhost:6379/0"
    POSTGRES_DSN: str = "postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph"
    
    # LLM API
    ANTHROPIC_API_KEY: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = True
        # Other services in this stack (Kafka, Postgres, pgAdmin, …) share the
        # Backend/.env; ignore vars this API doesn't declare rather than crash.
        extra = "ignore"

settings = Settings()