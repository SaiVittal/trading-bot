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


async def init_db() -> None:
    """
    Asynchronously initialize the database.
    Creates all defined SQLAlchemy models (users, signals, trades) if missing,
    and seeds a default administrator account.
    """
    from app.models.models import Base, User
    from app.core.auth import get_password_hash
    from sqlalchemy import select

    # 1. Asynchronously create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Seed a default administrator user if users table is empty
    async with async_session() as session:
        try:
            stmt = select(User).limit(1)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                admin_user = User(
                    username="admin",
                    email="admin@tradingplatform.local",
                    hashed_password=get_password_hash("adminpass123"),
                    role="admin",
                    is_active=True
                )
                session.add(admin_user)
                await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
