import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.redis_client import redis_client
from app.api.router import api_router
from app.services.market_feed import start_market_feed_simulation
from app.services.candle_engine import start_candle_engine

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup...")

    redis_client.initialize()
    if await redis_client.ping():
        logger.info("Redis connection verified.")
    else:
        logger.warning("Redis ping failed on startup.")

    logger.info("Spawning background tasks...")
    app.state.market_feed_task  = asyncio.create_task(start_market_feed_simulation())
    app.state.candle_engine_task = asyncio.create_task(start_candle_engine())

    yield

    logger.info("Application shutdown...")
    app.state.market_feed_task.cancel()
    app.state.candle_engine_task.cancel()
    await asyncio.gather(
        app.state.market_feed_task,
        app.state.candle_engine_task,
        return_exceptions=True,
    )
    await redis_client.close()
    logger.info("Shutdown complete.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Parse CORS origins from comma-separated env var.
# Per the CORS spec, allow_credentials=True requires explicit origins (not "*").
_origins      = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
_wildcard     = _origins == ["*"]
_allow_creds  = not _wildcard  # credentials only make sense with explicit origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    return {
        "message":   f"Welcome to the {settings.PROJECT_NAME} REST API",
        "endpoints": f"{settings.API_V1_STR}/health",
    }
