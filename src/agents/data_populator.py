from __future__ import annotations

from typing import Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
import sqlparse


class DataPopulator:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm

    def generate_data(
        self,
        scenario: Dict,
        tables: List[str],
        schema: Dict[str, List[str]],
    ) -> List[str]:
        """
        Returns a list of INSERT statements that create data matching the scenario.
        """
        if not tables or not schema:
            return []

        if not self.llm:
            return self._fallback_inserts(tables=tables, schema=schema)

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "You are a SQL test data generator for PostgreSQL. "
                        "Given a scenario and table schema, generate INSERT statements only. "
                        "Output SQL only, one statement per line, no markdown, no explanation."
                    ),
                ),
                (
                    "human",
                    (
                        "Scenario name: {scenario_name}\n"
                        "Scenario description: {scenario_description}\n"
                        "Tables: {tables}\n"
                        "Schema: {schema}\n"
                        "Generate INSERT statements in FK-safe order."
                    ),
                ),
            ]
        )

        try:
            response = (prompt | self.llm).invoke(
                {
                    "scenario_name": scenario.get("name", ""),
                    "scenario_description": scenario.get("description", ""),
                    "tables": tables,
                    "schema": schema,
                }
            )
            inserts = self._extract_insert_statements(str(response.content))
            if inserts:
                return inserts
        except Exception:
            pass
        return self._fallback_inserts(tables=tables, schema=schema)

    @staticmethod
    def _extract_insert_statements(raw_sql: str) -> List[str]:
        statements = []
        for stmt in sqlparse.split(raw_sql):
            normalized = stmt.strip()
            if not normalized:
                continue
            if normalized.upper().startswith("INSERT"):
                statements.append(normalized.rstrip(";") + ";")
        return statements

    @staticmethod
    def _fallback_inserts(tables: List[str], schema: Dict[str, List[str]]) -> List[str]:
        inserts: List[str] = []
        for table in tables:
            columns = schema.get(table, [])
            if not columns:
                continue

            values: List[str] = []
            for idx, column in enumerate(columns):
                col = column.lower()
                if col == "id" or col.endswith("_id"):
                    values.append(str(idx + 1))
                else:
                    values.append(f"'{table.replace('.', '_')}_{column}_1'")

            column_list = ", ".join(f'"{col}"' for col in columns)
            value_list = ", ".join(values)
            schema_name, table_name = DataPopulator._split_table_name(table)
            if schema_name == "public":
                relation = f'"{table_name}"'
            else:
                relation = f'"{schema_name}"."{table_name}"'
            inserts.append(f"INSERT INTO {relation} ({column_list}) VALUES ({value_list});")
        return inserts

    @staticmethod
    def _split_table_name(table_name: str) -> tuple[str, str]:
        cleaned = table_name.replace('"', "")
        if "." not in cleaned:
            return "public", cleaned
        schema_name, pure_table_name = cleaned.split(".", 1)
        return schema_name, pure_table_name
