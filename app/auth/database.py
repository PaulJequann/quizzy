from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings


@lru_cache
def auth_session_factory(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_auth_session() -> AsyncGenerator[AsyncSession]:
    factory = auth_session_factory(get_settings().database_url)
    async with factory() as session:
        yield session
