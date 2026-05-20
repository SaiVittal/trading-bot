from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings

# 1. Initialize the Asynchronous Engine
# Configured with standard pool sizing, overflow allowances, and pre-pings to avoid stale socket errors.
engine = create_async_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    future=True,
    echo=False
)

# 2. Bind the Async Session factory
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# 3. Create Session dependency generator for FastAPI dependency injection
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Asynchronous database session generator.
    Yields an AsyncSession and automatically closes it upon endpoint completion.
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
