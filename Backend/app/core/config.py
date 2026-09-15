from typing import List

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "FlowGraph Intelligence Engine"
    API_V1_STR: str = "/api"

    # CORS — an explicit allow-list, never a credentialed wildcard: that would let
    # any origin make authenticated cross-origin calls. Add your deployed frontend
    # here in prod.
    BACKEND_CORS_ORIGINS: List[str] = []
    # Loopback on ANY port is also allowed, because Vite silently moves to the next
    # free port when 5173 is taken and a self-hoster should not have to chase it.
    # Localhost-only, so this is not a wildcard.
    BACKEND_CORS_ORIGIN_REGEX: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    
    # Storage & Drivers
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "changeme"
    
    REDIS_URL: str = "redis://localhost:6379/0"
    POSTGRES_DSN: str = "postgresql+asyncpg://flowgraph:changeme@localhost:5432/flowgraph"
    
    # LLM API
    ANTHROPIC_API_KEY: str = ""

    # --- Explanation service (app/services/explanation_service.py) ---
    # LLM_PROVIDER: "anthropic" | "ollama" | "none" | "" (auto: key → anthropic,
    # else a reachable Ollama, else none). LLM_MODEL defaults per provider.
    LLM_PROVIDER: str = ""
    LLM_MODEL: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"

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

    # --- "Analyze a file" dataset upload (app/services/dataset_runner.py) ---
    UPLOAD_DIR: str = "uploads"
    UPLOAD_MAX_MB: int = 500
    # Features rebuilt from the stores for an uploaded graph land here and become
    # the runner's cache until the next upload.
    GNN_FEATURE_CACHE_UPLOAD: str = "ml/cache/featureset_upload.npz"

    # --- Live per-event GNN scoring (outbox hook) ---
    # Off by default: when enabled, each outbox sync cycle re-scores the accounts
    # its events touched plus their bounded k-hop neighborhood. LIVE_SCORE_HOPS
    # matches the champion depth (3); LIVE_MAX_AFFECTED caps the blast radius so a
    # hub event can't cascade unbounded.
    LIVE_SCORING_ENABLED: bool = False
    LIVE_SCORE_HOPS: int = 3
    LIVE_SCORE_FANOUT: int = 10
    LIVE_MAX_AFFECTED: int = 300

    class Config:
        env_file = ".env"
        case_sensitive = True
        # Other services in this stack (Kafka, Postgres, pgAdmin, …) share the
        # Backend/.env; ignore vars this API doesn't declare rather than crash.
        extra = "ignore"

settings = Settings()