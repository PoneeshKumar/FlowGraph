from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "FlowGraph Intelligence Engine"
    API_V1_STR: str = "/api"
    
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

settings = Settings()