import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.core.config import settings
from app.core.redis_client import redis_client
from app.api.router import api_router
from app.services.market_feed import start_market_feed_simulation
from app.services.candle_engine import start_candle_engine
from app.core.logging_config import setup_logging
from app.core.database import init_db

# 1. Initialize Sentry Observability SDK if DSN is set
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENV,
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1,  # Profile 10% of transaction paths
        profiles_sample_rate=0.1,
    )

# 2. Configure environment-specific structured logger
setup_logging()
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application startup...")

    # A. Verify PostgreSQL tables and seed default admin user
    try:
        logger.info("Initializing database tables and seeding defaults...")
        await init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Critical error during database initialization: {e}", exc_info=True)

    # B. Initialize Redis connection
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
