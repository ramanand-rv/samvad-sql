from __future__ import annotations

from functools import lru_cache
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL

from src.config import settings
from src.logging_config import get_logger, redact_db_url

logger = get_logger(__name__)


class DatabaseNotConfiguredError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    logger.debug("get_engine called; has_database=%s", settings.has_database)

    if not settings.has_database:
        raise DatabaseNotConfiguredError(
            "PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* settings in .env."
        )

    # Prefer an explicit DATABASE_URL when provided.
    database_url = (settings.database_url or "").strip()
    if database_url:
        final_url = database_url
    else:
        # Build URL from individual POSTGRES_* parts if present.
        if not (settings.postgres_host and settings.postgres_user and settings.postgres_db):
            raise DatabaseNotConfiguredError(
                "PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* settings in .env."
            )
        final_url = str(
            URL.create(
                "postgresql+psycopg2",
                username=settings.postgres_user,
                password=settings.postgres_password or None,
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
            )
        )

    logger.info("Creating SQLAlchemy engine -> %s", redact_db_url(final_url))

    engine = create_engine(
        final_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        future=True,
    )
    logger.debug("Engine created successfully")
    return engine


def try_get_engine() -> Optional[Engine]:
    try:
        return get_engine()
    except DatabaseNotConfiguredError:
        return None
