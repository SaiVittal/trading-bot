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

# 1. Setup Structured Application Logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app.main")

# 2. Define Lifespan Event Boundaries (Handles background workers and database session configurations)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup Sequence
    logger.info("Initializing application startup lifespans...")
    
    # Initialize asynchronous Redis connection pools
    redis_client.initialize()
    redis_connected = await redis_client.ping()
    if redis_connected:
        logger.info("Asynchronous Redis connection verified successfully.")
    else:
        logger.warning("FastAPI startup: Redis ping check failed.")
    
    # Spawn background real-time simulation tasks
    logger.info("Spawning background real-time event simulation tasks...")
    app.state.market_feed_task = asyncio.create_task(start_market_feed_simulation())
    app.state.candle_engine_task = asyncio.create_task(start_candle_engine())
        
    yield
    
    # Shutdown Sequence
    logger.info("Executing application shutdown boundaries...")
    
    # Cancel running background loops
    logger.info("Cancelling active background tasks...")
    app.state.market_feed_task.cancel()
    app.state.candle_engine_task.cancel()
    
    # Wait for clean task termination
    await asyncio.gather(
        app.state.market_feed_task,
        app.state.candle_engine_task,
        return_exceptions=True
    )
    
    # Terminate active storage pools
    await redis_client.close()
    logger.info("FastAPI service stopped clean.")


# 3. Instantiate the FastAPI Monolith app
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# 4. Mount CORS Middleware for frontend web accessibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to specific origins in actual production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. Route Mounting
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    return {
        "message": f"Welcome to the {settings.PROJECT_NAME} REST API",
        "endpoints": f"{settings.API_V1_STR}/health"
    }
