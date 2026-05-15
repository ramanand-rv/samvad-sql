from __future__ import annotations

from time import perf_counter
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.engine import Engine

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from src.agents.data_generator import DataGenerator
from src.db.introspection import ColumnInfo, get_table_columns, split_table_name, table_exists
from src.models import QueryAnalysis, ScenarioDefinition, ScenarioExecutionResult, ScenarioStatus
from src.logging_config import get_logger

logger = get_logger(__name__)
from src.config import settings


class ScenarioExecutor:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.data_generator = DataGenerator()

    def execute(
        self,
        sql_query: str,
        analysis: QueryAnalysis,
        scenarios: List[ScenarioDefinition],
        generate_missing_data: bool,
    ) -> List[ScenarioExecutionResult]:
        scenario_query = self._to_unqualified_query(sql_query)

        results: List[ScenarioExecutionResult] = []
        for scenario in scenarios:
            results.append(
                self._execute_single(
                    sql_query=scenario_query,
                    analysis=analysis,
                    scenario=scenario,
                    generate_missing_data=generate_missing_data,
                )
            )
        return results

    def _execute_single(
        self,
        sql_query: str,
        analysis: QueryAnalysis,
        scenario: ScenarioDefinition,
        generate_missing_data: bool,
    ) -> ScenarioExecutionResult:
        start = perf_counter()
        logger.info("Executing scenario '%s' for query (timeout=%dms)", scenario.scenario_id, settings.scenario_timeout_ms if hasattr(settings, 'scenario_timeout_ms') else 0)

        try:
            with self.engine.connect() as connection:
                logger.debug("Acquired DB connection for scenario %s", scenario.scenario_id)
                tx = connection.begin()
                try:
                    schema_snapshot = self._prepare_shadow_tables(connection, analysis.tables)

                    generated_rows = 0
                    if generate_missing_data:
                        rows_by_table = self.data_generator.generate_rows(
                            schema_snapshot=schema_snapshot,
                            scenario=scenario,
                            analysis=analysis,
                        )
                        generated_rows = self._insert_rows(connection, rows_by_table)
                        logger.debug("Inserted %d generated rows for scenario %s", generated_rows, scenario.scenario_id)

                    result = connection.execute(text(sql_query))
                    if result.returns_rows:
                        rows = result.mappings().fetchmany(20)
                        sample_rows = [dict(row) for row in rows]
                        row_count = len(sample_rows)
                    else:
                        row_count = int(result.rowcount or 0)
                        sample_rows = []

                    passed, reason = self._evaluate_expectation(scenario, row_count)
                    tx.rollback()

                    return ScenarioExecutionResult(
                        scenario_id=scenario.scenario_id,
                        name=scenario.name,
                        status=ScenarioStatus.passed if passed else ScenarioStatus.failed,
                        reason=reason,
                        execution_time_ms=round((perf_counter() - start) * 1000, 2),
                        row_count=row_count,
                        sample_rows=sample_rows,
                        generated_rows=generated_rows,
                    )
                except Exception as exc:
                    tx.rollback()
                    return ScenarioExecutionResult(
                        scenario_id=scenario.scenario_id,
                        name=scenario.name,
                        status=ScenarioStatus.failed,
                        reason=str(exc),
                        execution_time_ms=round((perf_counter() - start) * 1000, 2),
                        generated_rows=0,
                    )
        except Exception as exc:
            return ScenarioExecutionResult(
                scenario_id=scenario.scenario_id,
                name=scenario.name,
                status=ScenarioStatus.failed,
                reason=str(exc),
                execution_time_ms=round((perf_counter() - start) * 1000, 2),
                generated_rows=0,
            )

    def _prepare_shadow_tables(
        self,
        connection,
        tables: List[str],
    ) -> Dict[str, List[ColumnInfo]]:
        schema_snapshot: Dict[str, List[ColumnInfo]] = {}

        for table in tables:
            if not table_exists(connection, table):
                raise ValueError(f"Table not found: {table}")

            schema_name, table_name = split_table_name(table)
            source_table = f'"{schema_name}"."{table_name}"'
            target_table = f'"{table_name}"'

            # Temporary table shadows the real one for unqualified references.
            logger.debug("Creating TEMP TABLE %s from %s", target_table, source_table)
            connection.execute(
                text(f"CREATE TEMP TABLE {target_table} (LIKE {source_table} INCLUDING ALL) ON COMMIT DROP")
            )
            schema_snapshot[table] = get_table_columns(connection, table)

        return schema_snapshot

    @staticmethod
    def _insert_rows(connection, rows_by_table: Dict[str, List[dict]]) -> int:
        total = 0

        for full_table_name, rows in rows_by_table.items():
            _, table_name = split_table_name(full_table_name)
            if not rows:
                continue

            for row in rows:
                columns = list(row.keys())
                if not columns:
                    continue
                quoted_columns = ", ".join(f'"{column}"' for column in columns)
                placeholders = ", ".join(f":{column}" for column in columns)
                stmt = text(
                    f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({placeholders})'
                )
                logger.debug("Inserting row into %s: %s", table_name, {k: (v if isinstance(v,(int,str)) else str(v)) for k,v in row.items()})
                connection.execute(stmt, row)
                total += 1

        return total

    @staticmethod
    def _evaluate_expectation(scenario: ScenarioDefinition, row_count: int) -> tuple[bool, str | None]:
        if scenario.expectation == "row_count_eq_zero":
            passed = row_count == 0
            return passed, None if passed else f"Expected zero rows, got {row_count}."

        if scenario.expectation == "row_count_gt_zero":
            passed = row_count > 0
            return passed, None if passed else "Expected at least one row, got zero."

        # Default: execution success means no runtime exception.
        return True, None

    @staticmethod
    def _to_unqualified_query(sql_query: str) -> str:
        try:
            expression = parse_one(sql_query, read="postgres")
        except ParseError:
            return sql_query

        def _transform(node: exp.Expression):
            if isinstance(node, exp.Table):
                node.set("db", None)
                return node
            return node

        transformed = expression.transform(_transform)
        return transformed.sql(dialect="postgres")
