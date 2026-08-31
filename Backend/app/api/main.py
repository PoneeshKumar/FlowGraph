# backend/app/api/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.endpoints import router as api_router
from app.db.neo4j import neo4j_client
from app.db.redis import redis_pool
from app.viz.router import router as viz_router
from app.viz import deps as viz_deps

@asynccontextmanager
async def lifespan(app: FastAPI):
    neo4j_client.connect()
    await viz_deps.startup()
    yield
    await viz_deps.shutdown()
    await neo4j_client.close()
    await redis_pool.disconnect()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# Explicit allow-list, not "*": a credentialed wildcard lets any origin make
# authenticated cross-origin calls. Origins come from settings (env-overridable).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(viz_router, prefix="/viz")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "FlowGraph Engine"}