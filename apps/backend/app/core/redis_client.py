import logging
from typing import Optional, Any, cast
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger("app.core.redis")

class AsyncRedisClient:
    def __init__(self) -> None:
        self.pool: Optional[aioredis.ConnectionPool] = None
        self.client: Optional[aioredis.Redis] = None

    def initialize(self) -> None:
        """
        Creates the asynchronous connection pool using the environment Redis URL.
        """
        logger.info(f"Connecting to Redis pool at: {settings.REDIS_URL}")
        self.pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=50
        )
        self.client = aioredis.Redis(connection_pool=self.pool)

    async def ping(self) -> bool:
        """
        Performs a ping health check on the Redis client.
        """
        if not self.client:
            return False
        try:
            return await cast(Any, self.client.ping())
        except Exception as e:
            logger.error(f"Redis health check failed: {str(e)}")
            return False

    async def close(self) -> None:
        """
        Closes all active connections in the connection pool.
        """
        if self.pool:
            logger.info("Closing Redis connection pool...")
            await self.pool.disconnect()
            self.pool = None
            self.client = None


# Single global instance of the redis client manager
redis_client = AsyncRedisClient()
