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

    # --- Community visualiser (/viz) ---
    GNN_RUN_DIR: str = "ml/runs/v10_L3"
    # Extra checkpoints averaged with GNN_RUN_DIR into a seed ensemble. Averaging
    # cancels each model's idiosyncratic false positives: at matched recall (~0.55)
    # this cuts whole-graph FP ~505→385 and lifts PR-AUC 0.687→0.710. Members that
    # aren't present on disk are skipped (see PipelineRunner._gnn), so serving
    # degrades gracefully to the single champion.
    GNN_ENSEMBLE_RUNS: List[str] = ["ml/runs/v10_L3_s1", "ml/runs/v10_L3_s7"]
    GNN_FEATURE_CACHE: str = "ml/cache/featureset_v4.npz"
    MARK_GNN_THRESHOLD: float = 0.5
    CYCLE_MAX_SEEDS: int = 500

    class Config:
        env_file = ".env"
        case_sensitive = True
        # Other services in this stack (Kafka, Postgres, pgAdmin, …) share the
        # Backend/.env; ignore vars this API doesn't declare rather than crash.
        extra = "ignore"

settings = Settings()