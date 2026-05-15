from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    is_nullable: bool
    column_default: str | None


def split_table_name(table_name: str) -> Tuple[str, str]:
    if "." in table_name:
        schema, table = table_name.split(".", 1)
        return schema.strip('"'), table.strip('"')
    return "public", table_name.strip('"')


def table_exists(connection: Connection, table_name: str) -> bool:
    schema, table = split_table_name(table_name)
    query = text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = :schema_name
              AND table_name = :table_name
        )
        """
    )
    exists = bool(
        connection.execute(query, {"schema_name": schema, "table_name": table}).scalar()
    )
    logger.debug("table_exists: %s -> %s", table_name, exists)
    return exists


def get_table_columns(connection: Connection, table_name: str) -> List[ColumnInfo]:
    schema, table = split_table_name(table_name)
    query = text(
        """
        SELECT
            column_name,
            data_type,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = :schema_name
          AND table_name = :table_name
        ORDER BY ordinal_position
        """
    )
    rows = connection.execute(query, {"schema_name": schema, "table_name": table}).mappings()
    columns = [
        ColumnInfo(
            name=row["column_name"],
            data_type=row["data_type"],
            is_nullable=row["is_nullable"] == "YES",
            column_default=row["column_default"],
        )
        for row in rows
    ]
    logger.debug("get_table_columns %s.%s -> %d columns", schema, table, len(columns))
    return columns


def load_schema_snapshot(engine: Engine, tables: List[str]) -> Dict[str, List[ColumnInfo]]:
    snapshot: Dict[str, List[ColumnInfo]] = {}
    with engine.connect() as connection:
        for table in tables:
            snapshot[table] = get_table_columns(connection, table)
            logger.debug("Loaded schema snapshot for %s -> %d cols", table, len(snapshot[table]))
    return snapshot
