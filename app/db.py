from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


# Explicit pool tuning so we don't drift under load:
#   pool_size=10        — steady connections per worker
#   max_overflow=10     — burst capacity
#   pool_timeout=5      — fail fast under contention instead of hanging
#                         a request behind dozens of waiters
#   pool_recycle=1800   — refresh half-hourly so PG/proxy idle timeouts
#                         don't surface as dead connections
#   pool_pre_ping=True  — cheap SELECT 1 before checkout to catch a
#                         dead connection before the route uses it
# Two replicas × 20 max = 40 conns; well under Postgres default 100.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=10,
    pool_timeout=5,
    pool_recycle=1800,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
