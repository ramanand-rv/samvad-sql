from __future__ import annotations

from pathlib import Path

from psycopg2 import sql

from src.config import settings
from src.db.temp_db_manager_pg import PostgresTempDBManager
from src.logging_config import get_logger

logger = get_logger(__name__)


def setup_template_database(schema_file: str = "src/db/schema.sql") -> None:
    manager = PostgresTempDBManager(template_db=settings.test_db_template)
    schema_sql = Path(schema_file).read_text(encoding="utf-8")
    logger.info("Setting up template database '%s' from %s", settings.test_db_template, schema_file)
    with manager._admin_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s
                  AND pid <> pg_backend_pid()
                """,
                (settings.test_db_template,),
            )
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(settings.test_db_template)
                )
            )
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(settings.test_db_template)
                )
            )

    with manager._connection(dbname=settings.test_db_template) as template_conn:
        with template_conn.cursor() as template_cur:
            template_cur.execute(schema_sql)
        template_conn.commit()
    logger.info("Template database '%s' is ready.", settings.test_db_template)


if __name__ == "__main__":
    setup_template_database()
