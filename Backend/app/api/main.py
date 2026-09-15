# backend/app/api/main.py
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.config import settings
from app.api.endpoints import router as api_router
from app.db.neo4j import neo4j_client
from app.db.redis import redis_pool
from app.services import stats_service, explanation_service
from app.viz import metrics as viz_metrics
from app.viz.router import router as viz_router, api as viz_api_router
from app.viz import deps as viz_deps

# uvicorn configures only its own loggers, so without this every logger.info in
# the app (cache warm-up, chosen explanation provider, pipeline stages) is
# invisible to whoever is running the server.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("api")


async def _warm_caches() -> None:
    """Build the two whole-graph caches off the request path.

    Both are one big scan and then pure in-memory work: stats is a ~8 s pass over
    5M TRANSFER edges, metrics loads 514k GNN scores (the threshold slider's
    precision/recall came to 20 s cold and 0.05 s warm, so the Pipeline view would
    otherwise stall on its first visit). Failures are logged, not raised — the
    endpoints answer 503 / recompute on demand.
    """
    session = neo4j_client.driver.session
    await stats_service.warmup(session, viz_deps.pg())
    try:
        await viz_metrics.ensure_loaded(session)
    except Exception:  # noqa: BLE001 — logged; /metrics recomputes on demand
        logger.exception("metrics cache warm-up failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    neo4j_client.connect()
    await viz_deps.startup()
    warm = asyncio.create_task(_warm_caches())
    await explanation_service.configure(settings)   # pick ollama / anthropic / rule-based once
    yield
    warm.cancel()
    await viz_deps.shutdown()
    await neo4j_client.close()
    await redis_pool.disconnect()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# Explicit allow-list plus a loopback regex, never "*": a credentialed wildcard
# lets any origin make authenticated cross-origin calls. Both come from settings
# (env-overridable) — see app/core/config.py for why the regex exists.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_origin_regex=settings.BACKEND_CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(viz_router, prefix="/viz")
app.include_router(viz_api_router, prefix="/api/pipeline")   # same JSON, one base URL for the frontend
app.mount(
    "/viz/static",
    StaticFiles(directory=Path(__file__).parent.parent / "viz" / "static"),
    name="viz-static",
)

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "FlowGraph Engine"}