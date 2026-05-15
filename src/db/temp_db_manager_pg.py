from __future__ import annotations

import uuid
from typing import Dict, List, Tuple

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from src.config import settings


class PostgresTempDBManager:
    """Creates isolated temporary PostgreSQL databases for scenario execution."""

    def __init__(self, template_db: str | None = None):
        self.base_db = settings.postgres_db
        self.template_db = template_db or settings.test_db_template or self.base_db

    def create_scenario_db(self) -> str:
        db_name = f"scenario_{uuid.uuid4().hex[:10]}"
        with self._admin_connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                        sql.Identifier(db_name),
                        sql.Identifier(self.template_db),
                    )
                )
        return db_name

    def drop_scenario_db(self, db_name: str) -> None:
        with self._admin_connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s
                      AND pid <> pg_backend_pid()
                    """,
                    (db_name,),
                )
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        sql.Identifier(db_name)
                    )
                )

    def execute_inserts(self, db_name: str, insert_statements: List[str]) -> None:
        if not insert_statements:
            return
        with self._connection(dbname=db_name) as conn:
            with conn.cursor() as cur:
                for stmt in insert_statements:
                    if stmt.strip():
                        cur.execute(stmt)
            conn.commit()

    def execute_query(self, db_name: str, query: str) -> List[Dict]:
        with self._connection(dbname=db_name) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query)
                if cur.description is None:
                    conn.commit()
                    return []
                rows = cur.fetchall()
                return [dict(row) for row in rows]

    def execute_in_transaction(
        self,
        insert_statements: List[str],
        query: str,
    ) -> List[Dict]:
        """
        Execute scenario in a single transaction on template DB and always rollback.

        This provides fast, isolated execution without creating/dropping databases.
        """
        conn = self._connection(dbname=self.base_db)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("BEGIN")
                cur.execute(
                    "SET LOCAL statement_timeout = %s",
                    (str(settings.scenario_timeout_ms),),
                )
                for stmt in insert_statements:
                    if stmt.strip():
                        cur.execute(stmt)

                cur.execute(query)
                if cur.description is None:
                    rows: List[Dict] = []
                else:
                    rows = [dict(row) for row in cur.fetchall()]

                conn.rollback()
                return rows
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_table_schema(self, tables: List[str]) -> Dict[str, List[str]]:
        schema: Dict[str, List[str]] = {}
        if not tables:
            return schema

        with self._connection(dbname=self.base_db) as conn:
            with conn.cursor() as cur:
                for raw_table in tables:
                    schema_name, table_name = self._split_table_name(raw_table)
                    cur.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = %s
                          AND table_name = %s
                        ORDER BY ordinal_position
                        """,
                        (schema_name, table_name),
                    )
                    columns = [row[0] for row in cur.fetchall()]
                    if columns:
                        schema[raw_table] = columns
        return schema

    def list_user_schemas(self) -> List[str]:
        with self._connection(dbname=self.base_db) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name NOT IN ('information_schema', 'pg_catalog')
                    ORDER BY schema_name
                    """
                )
                return [row[0] for row in cur.fetchall()]

    def get_erd_metadata(self, schema_name: str) -> Dict:
        with self._connection(dbname=self.base_db) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH target_schema AS (
                        SELECT %s::text AS schema_name
                    ),
                    all_columns AS (
                        SELECT
                            c.table_schema,
                            c.table_name,
                            c.column_name,
                            c.data_type,
                            c.is_nullable,
                            c.ordinal_position,
                            COALESCE(pk.is_primary_key, FALSE) as is_primary_key
                        FROM information_schema.columns c
                        CROSS JOIN target_schema ts
                        JOIN information_schema.tables t
                            ON c.table_schema = t.table_schema
                           AND c.table_name = t.table_name
                        LEFT JOIN (
                            SELECT
                                kcu.table_schema,
                                kcu.table_name,
                                kcu.column_name,
                                TRUE as is_primary_key
                            FROM information_schema.table_constraints tc
                            JOIN information_schema.key_column_usage kcu
                                ON tc.constraint_name = kcu.constraint_name
                               AND tc.table_schema = kcu.table_schema
                            WHERE tc.constraint_type = 'PRIMARY KEY'
                        ) pk
                            ON c.table_schema = pk.table_schema
                           AND c.table_name = pk.table_name
                           AND c.column_name = pk.column_name
                        WHERE c.table_schema = ts.schema_name
                          AND t.table_type = 'BASE TABLE'
                    ),
                    foreign_keys AS (
                        SELECT
                            kcu.table_schema,
                            kcu.table_name,
                            kcu.column_name,
                            ccu.table_schema AS foreign_table_schema,
                            ccu.table_name AS foreign_table_name,
                            ccu.column_name AS foreign_column_name,
                            tc.constraint_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                            ON tc.constraint_name = kcu.constraint_name
                           AND tc.table_schema = kcu.table_schema
                        JOIN information_schema.constraint_column_usage ccu
                            ON ccu.constraint_name = tc.constraint_name
                           AND ccu.table_schema = tc.table_schema
                        CROSS JOIN target_schema ts
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                          AND kcu.table_schema = ts.schema_name
                    )
                    SELECT jsonb_build_object(
                        'database', current_database(),
                        'schema', ts.schema_name,
                        'tables', COALESCE((
                            SELECT jsonb_agg(
                                jsonb_build_object(
                                    'name', t.table_name,
                                    'columns', (
                                        SELECT jsonb_agg(
                                            jsonb_build_object(
                                                'name', ac.column_name,
                                                'type', ac.data_type,
                                                'nullable', ac.is_nullable = 'YES',
                                                'is_primary_key', ac.is_primary_key,
                                                'ordinal', ac.ordinal_position
                                            )
                                            ORDER BY ac.ordinal_position
                                        )
                                        FROM all_columns ac
                                        WHERE ac.table_name = t.table_name
                                    ),
                                    'foreign_keys', (
                                        SELECT jsonb_agg(
                                            jsonb_build_object(
                                                'column', fk.column_name,
                                                'references', jsonb_build_object(
                                                    'table', fk.foreign_table_name,
                                                    'column', fk.foreign_column_name
                                                )
                                            )
                                        )
                                        FROM foreign_keys fk
                                        WHERE fk.table_name = t.table_name
                                    )
                                )
                            )
                            FROM (SELECT DISTINCT table_name FROM all_columns) t
                        ), '[]'::jsonb)
                    ) AS erd_data
                    FROM target_schema ts;
                    """,
                    (schema_name,),
                )
                row = cur.fetchone()
                return row[0] if row and row[0] else {"database": None, "schema": schema_name, "tables": []}

    @staticmethod
    def _split_table_name(table_name: str) -> Tuple[str, str]:
        if "." not in table_name:
            return "public", table_name.strip('"')
        schema_name, pure_table_name = table_name.split(".", 1)
        return schema_name.strip('"'), pure_table_name.strip('"')

    def _connection(self, dbname: str, autocommit: bool = False):
        conn = psycopg2.connect(**self._connection_kwargs(dbname))
        conn.autocommit = autocommit
        return conn

    def _admin_connection(self, autocommit: bool = False):
        return self._connection(dbname=settings.postgres_admin_db, autocommit=autocommit)

    @staticmethod
    def _connection_kwargs(dbname: str) -> Dict:
        has_explicit_pg = bool(settings.postgres_host and settings.postgres_user)
        if has_explicit_pg:
            kwargs: Dict = {
                "host": settings.postgres_host,
                "port": settings.postgres_port,
                "user": settings.postgres_user,
                "password": settings.postgres_password,
                "dbname": dbname,
            }
            if settings.postgres_sslmode:
                kwargs["sslmode"] = settings.postgres_sslmode
            return kwargs

        if settings.database_url:
            return {"dsn": settings.database_url, "dbname": dbname}

        return {"dbname": dbname}
