from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Dict, List, Optional, Set, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from src.db.temp_db_manager_pg import PostgresTempDBManager
from src.config import settings
from src.logging_config import get_logger

logger = get_logger(__name__)


class TestExecutor:
    def __init__(self, llm: Optional[BaseChatModel], db_manager: PostgresTempDBManager):
        self.llm = llm
        self.db_manager = db_manager

    def run_scenario(
        self,
        scenario: Dict,
        user_sql: str,
        insert_statements: List[str],
    ) -> Dict:
        """
        Returns:
            {
                "passed": bool,
                "actual_results": list,
                "expected_results": list,
                "failure_reason": str | None,
                "execution_time_ms": float
            }
        """
        db_name: Optional[str] = None
        start = perf_counter()
        working_statements = list(insert_statements)
        isolation_mode = self._choose_isolation_mode(user_sql, working_statements)
        fallback_reason = None
        if (
            settings.test_isolation_mode == "transaction"
            and isolation_mode == "database"
            and settings.test_isolation_auto_fallback
        ):
            fallback_reason = "Auto-fallback to per-database isolation for non-transaction-safe SQL."
        attempted_repairs: Set[str] = set()
        last_error: Optional[Exception] = None
        retry_notes: List[str] = []
        logger.info("Running scenario '%s' (isolation_mode=%s)", scenario.get("name"), isolation_mode)
        try:
            actual: List[Dict] = []
            max_attempts = max(1, settings.max_retry_attempts + 1)
            for attempt in range(1, max_attempts + 1):
                db_name = None
                try:
                    if isolation_mode == "transaction":
                        actual = self.db_manager.execute_in_transaction(
                            insert_statements=working_statements,
                            query=user_sql,
                        )
                    else:
                        db_name = self.db_manager.create_scenario_db()
                        logger.debug("Created scenario DB: %s", db_name)
                        self.db_manager.execute_inserts(db_name, working_statements)
                        actual = self.db_manager.execute_query(db_name, user_sql)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if db_name:
                        try:
                            self.db_manager.drop_scenario_db(db_name)
                        except Exception:
                            pass
                        db_name = None

                    repair_sql, repair_note = self._repair_statements_for_error(
                        error=exc,
                        user_sql=user_sql,
                        already_applied=attempted_repairs,
                    )
                    if not repair_sql:
                        break
                    retry_notes.append(
                        f"Retry {attempt}: {repair_note}"
                    )
                    for stmt in repair_sql:
                        if stmt not in working_statements:
                            working_statements.append(stmt)

                    isolation_mode = self._choose_isolation_mode(user_sql, working_statements)
            if last_error is not None:
                raise last_error

            expected = self._generate_expected(scenario, user_sql, working_statements)

            passed = self._rows_equal(actual, expected)
            failure_reason = None
            if not passed:
                failure_reason = f"Expected {expected}, got {actual}"
            if retry_notes:
                retry_text = " ".join(retry_notes)
                failure_reason = f"{retry_text} {failure_reason}" if failure_reason else retry_text
            if fallback_reason:
                failure_reason = (
                    f"{fallback_reason} {failure_reason}" if failure_reason else fallback_reason
                )

            return {
                "passed": passed,
                "actual_results": actual,
                "expected_results": expected,
                "failure_reason": failure_reason,
                "isolation_mode": isolation_mode,
                "execution_time_ms": round((perf_counter() - start) * 1000, 2),
            }
        except Exception as exc:
            return {
                "passed": False,
                "actual_results": [],
                "expected_results": [],
                "failure_reason": self._format_execution_error(exc),
                "isolation_mode": isolation_mode,
                "execution_time_ms": round((perf_counter() - start) * 1000, 2),
            }
        finally:
            if db_name:
                try:
                    self.db_manager.drop_scenario_db(db_name)
                except Exception:
                    # Best effort cleanup only.
                    pass

    def _generate_expected(
        self,
        scenario: Dict,
        user_sql: str,
        insert_statements: List[str],
    ) -> List[Dict]:
        if not self.llm:
            return []

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "Given a SQL query and INSERT statements that populate a PostgreSQL database, "
                        "predict the exact result rows. Return only JSON array of objects."
                    ),
                ),
                (
                    "human",
                    (
                        "Scenario: {scenario_description}\n"
                        "Data INSERTs:\n{insert_statements}\n"
                        "Query:\n{query}\n"
                        'Return only JSON, e.g. [{"col1": "value1"}] or [].'
                    ),
                ),
            ]
        )

        try:
            response = (prompt | self.llm).invoke(
                {
                    "scenario_description": scenario.get("description", ""),
                    "insert_statements": "\n".join(insert_statements),
                    "query": user_sql,
                }
            )
        except Exception:
            return []
        raw = str(response.content).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("[")
            end = raw.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    return []
            return []

    @staticmethod
    def _rows_equal(actual: List[Dict], expected: List[Dict]) -> bool:
        return actual == expected

    def _repair_statements_for_error(
        self,
        error: Exception,
        user_sql: str,
        already_applied: Set[str],
    ) -> Tuple[List[str], str]:
        message = str(error)
        missing_relations = self._extract_missing_relations(message)
        if not missing_relations:
            return [], ""

        new_relations = [relation for relation in missing_relations if relation not in already_applied]
        if not new_relations:
            return [], ""

        ddl_and_seed = self._build_missing_relation_statements(user_sql, new_relations)
        for relation in new_relations:
            already_applied.add(relation)
        if not ddl_and_seed:
            return [], ""
        return ddl_and_seed, f"created missing relations ({', '.join(new_relations)}) and retried."

    @staticmethod
    def _extract_missing_relations(error_text: str) -> List[str]:
        # Example: relation "employees.salary" does not exist
        matches = re.findall(r'relation\s+"([^"]+)"\s+does\s+not\s+exist', error_text, flags=re.IGNORECASE)
        return sorted(set(matches))

    def _build_missing_relation_statements(
        self,
        user_sql: str,
        missing_relations: List[str],
    ) -> List[str]:
        columns_by_relation = self._infer_columns_by_relation(user_sql)
        statements: List[str] = []

        for relation in missing_relations:
            schema_name, table_name = self._split_relation_name(relation)
            relation_key = f"{schema_name}.{table_name}"
            table_columns = columns_by_relation.get(relation_key, set()) or {"id"}

            statements.append(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";')
            column_defs = ", ".join(
                f'"{column}" {self._guess_column_type(column)}'
                for column in sorted(table_columns)
            )
            statements.append(
                f'CREATE TABLE IF NOT EXISTS "{schema_name}"."{table_name}" ({column_defs});'
            )

            insert_cols = sorted(table_columns)[:6]
            if insert_cols:
                column_list = ", ".join(f'"{c}"' for c in insert_cols)
                row1 = self._seed_row_values(insert_cols, row_index=1)
                row2 = self._seed_row_values(insert_cols, row_index=2)
                statements.append(
                    f'INSERT INTO "{schema_name}"."{table_name}" ({column_list}) VALUES ({row1}), ({row2});'
                )

        return statements

    def _infer_columns_by_relation(self, user_sql: str) -> Dict[str, Set[str]]:
        try:
            expression = parse_one(user_sql, read="postgres")
        except ParseError:
            return {}

        alias_to_relation: Dict[str, str] = {}
        relation_to_columns: Dict[str, Set[str]] = {}

        for table in expression.find_all(exp.Table):
            if table.db:
                relation = f"{table.db}.{table.name}".replace('"', "")
            else:
                relation = table.name.replace('"', "")
            alias = table.alias_or_name
            if alias:
                alias_to_relation[alias] = relation
            alias_to_relation[table.name] = relation
            relation_to_columns.setdefault(relation, set())

        for column in expression.find_all(exp.Column):
            column_name = column.name
            table_alias = column.table
            if not column_name:
                continue
            target_relation = None
            if table_alias:
                target_relation = alias_to_relation.get(table_alias) or alias_to_relation.get(table_alias.strip('"'))
            if target_relation:
                relation_to_columns.setdefault(target_relation, set()).add(column_name)

        normalized: Dict[str, Set[str]] = {}
        for relation, cols in relation_to_columns.items():
            schema_name, table_name = self._split_relation_name(relation)
            normalized[f"{schema_name}.{table_name}"] = set(cols)
        return normalized

    @staticmethod
    def _split_relation_name(relation: str) -> Tuple[str, str]:
        cleaned = relation.strip().replace('"', "")
        if "." not in cleaned:
            return "public", cleaned
        schema_name, table_name = cleaned.split(".", 1)
        return schema_name, table_name

    @staticmethod
    def _guess_column_type(column_name: str) -> str:
        name = column_name.lower()
        if name == "id" or name.endswith("_id"):
            return "INTEGER"
        if "amount" in name or "salary" in name or "price" in name or "total" in name:
            return "NUMERIC(12,2)"
        if name.endswith("_date") or "date" in name:
            return "DATE"
        if "count" in name:
            return "INTEGER"
        return "TEXT"

    def _seed_row_values(self, columns: List[str], row_index: int) -> str:
        values: List[str] = []
        for column in columns:
            dtype = self._guess_column_type(column)
            col = column.lower()
            if dtype.startswith("INTEGER"):
                values.append(str(row_index))
            elif dtype.startswith("NUMERIC"):
                values.append(str(1000 + (row_index * 100)))
            elif dtype == "DATE":
                if col.endswith("to_date") or col == "to_date":
                    values.append("CURRENT_DATE + INTERVAL '365 days'")
                elif col.endswith("from_date") or col == "from_date":
                    values.append("CURRENT_DATE - INTERVAL '365 days'")
                else:
                    values.append("CURRENT_DATE")
            else:
                values.append(f"'sample_{column}_{row_index}'")
        return ", ".join(values)

    @staticmethod
    def _format_execution_error(exc: Exception) -> str:
        text = str(exc)
        if "password authentication failed" in text.lower():
            return (
                "Execution error: PostgreSQL authentication failed. "
                "Verify POSTGRES_USER/POSTGRES_PASSWORD (or DATABASE_URL) for the target database."
            )
        return f"Execution error: {text}"

    def _choose_isolation_mode(
        self,
        user_sql: str,
        insert_statements: List[str],
    ) -> str:
        configured_mode = settings.test_isolation_mode
        if configured_mode == "database":
            return "database"

        if not settings.test_isolation_auto_fallback:
            return "transaction"

        all_statements = [user_sql] + list(insert_statements)
        if self._requires_database_isolation(all_statements):
            return "database"
        return "transaction"

    @staticmethod
    def _requires_database_isolation(statements: List[str]) -> bool:
        non_transactional_patterns = [
            r"\bCREATE\s+DATABASE\b",
            r"\bDROP\s+DATABASE\b",
            r"\bVACUUM\b",
            r"\bALTER\s+SYSTEM\b",
            r"\bREINDEX\s+DATABASE\b",
            r"\bCOMMIT\b",
            r"\bROLLBACK\b",
            r"\bBEGIN\b",
            r"\bSTART\s+TRANSACTION\b",
            r"\bCREATE\s+INDEX\s+CONCURRENTLY\b",
            r"\bREFRESH\s+MATERIALIZED\s+VIEW\s+CONCURRENTLY\b",
        ]

        schema_risk_patterns = [
            r"\bDROP\s+TABLE\b",
            r"\bALTER\s+TABLE\b",
            r"\bTRUNCATE\b",
        ]

        compiled_non_tx = [re.compile(pattern, flags=re.IGNORECASE) for pattern in non_transactional_patterns]
        compiled_schema_risk = [re.compile(pattern, flags=re.IGNORECASE) for pattern in schema_risk_patterns]

        for stmt in statements:
            normalized = stmt.strip()
            if not normalized:
                continue
            if any(pattern.search(normalized) for pattern in compiled_non_tx):
                return True
            if any(pattern.search(normalized) for pattern in compiled_schema_risk):
                return True
        return False
