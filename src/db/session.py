from __future__ import annotations

from functools import lru_cache
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL

from src.config import settings


class DatabaseNotConfiguredError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    if not settings.has_database:
        raise DatabaseNotConfiguredError(
            "PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* settings in .env."
        )

    has_explicit_pg = bool(settings.postgres_host and settings.postgres_user and settings.postgres_db)
    database_url = settings.database_url.strip()
    if has_explicit_pg:
        database_url = str(
            URL.create(
                "postgresql+psycopg2",
                username=settings.postgres_user,
                password=settings.postgres_password or None,
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
            )
        )
    elif not database_url:
        raise DatabaseNotConfiguredError(
            "PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* settings in .env."
        )

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        future=True,
    )


def try_get_engine() -> Optional[Engine]:
    try:
        return get_engine()
    except DatabaseNotConfiguredError:
        return None
