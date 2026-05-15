from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Set, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from src.models import QueryAnalysis


class SQLRepairAgent:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm

    def repair_for_testing(
        self,
        sql_query: str,
        analysis: QueryAnalysis,
        erd_by_schema: Dict[str, Dict],
    ) -> Tuple[str, List[str]]:
        notes: List[str] = []
        normalized = sql_query.strip().rstrip(";")
        if not normalized:
            return sql_query, notes

        has_from = bool(re.search(r"\bFROM\b", normalized, flags=re.IGNORECASE))
        needs_from_repair = (
            analysis.query_type.upper() == "SELECT"
            and not has_from
            and bool(analysis.columns)
        )

        if needs_from_repair:
            repaired = self._repair_missing_from(sql_query=normalized, analysis=analysis, erd_by_schema=erd_by_schema)
            if repaired and repaired.lower() != normalized.lower():
                notes.append("Auto-repaired SQL by inferring FROM/JOIN tables from schema metadata.")
                normalized = repaired

        group_fixed = self._ensure_group_by_for_aggregates(normalized)
        if group_fixed and group_fixed.lower() != normalized.lower():
            notes.append("Auto-added GROUP BY for non-aggregated selected columns.")
            normalized = group_fixed

        if analysis.parse_error and self.llm and erd_by_schema:
            llm_sql = self._llm_repair(sql_query, analysis.parse_error, erd_by_schema)
            if llm_sql and llm_sql.lower() != normalized.lower():
                notes.append("Auto-repaired SQL via LLM using schema metadata.")
                return llm_sql.rstrip(";") + ";", notes

        return normalized.rstrip(";") + ";", notes

    def _repair_missing_from(
        self,
        sql_query: str,
        analysis: QueryAnalysis,
        erd_by_schema: Dict[str, Dict],
    ) -> str:
        alias_columns = self._extract_alias_columns(analysis.columns)
        if not alias_columns:
            return sql_query

        best_schema = self._pick_best_schema(alias_columns, erd_by_schema)
        if not best_schema:
            return sql_query

        erd = erd_by_schema[best_schema]
        table_map = self._map_alias_to_table(alias_columns, erd)
        if not table_map:
            return sql_query

        from_join_clause = self._build_from_join_clause(best_schema, table_map, erd)
        if not from_join_clause:
            return sql_query

        select_part, tail_part = self._split_select_and_tail(sql_query)
        rebuilt = f"{select_part}\n{from_join_clause}"
        if tail_part:
            rebuilt = f"{rebuilt}\n{tail_part}"
        return rebuilt

    @staticmethod
    def _split_select_and_tail(sql_query: str) -> Tuple[str, str]:
        pattern = re.compile(r"\b(WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT)\b", flags=re.IGNORECASE)
        match = pattern.search(sql_query)
        if not match:
            return sql_query.strip(), ""
        idx = match.start()
        return sql_query[:idx].strip(), sql_query[idx:].strip()

    @staticmethod
    def _extract_alias_columns(columns: List[str]) -> Dict[str, Set[str]]:
        alias_columns: Dict[str, Set[str]] = {}
        for column in columns:
            cleaned = column.replace('"', "")
            if "." not in cleaned:
                continue
            alias, column_name = cleaned.split(".", 1)
            alias = alias.strip()
            column_name = column_name.strip()
            if not alias or not column_name:
                continue
            alias_columns.setdefault(alias, set()).add(column_name)
        return alias_columns

    def _pick_best_schema(self, alias_columns: Dict[str, Set[str]], erd_by_schema: Dict[str, Dict]) -> Optional[str]:
        best_schema = None
        best_score = -1
        requested_columns = set()
        for cols in alias_columns.values():
            requested_columns.update(cols)

        for schema_name, erd in erd_by_schema.items():
            score = 0
            for table in erd.get("tables", []):
                table_cols = {c.get("name") for c in table.get("columns", [])}
                score += len(requested_columns & table_cols)
            if score > best_score:
                best_score = score
                best_schema = schema_name
        return best_schema if best_score > 0 else None

    def _map_alias_to_table(
        self,
        alias_columns: Dict[str, Set[str]],
        erd: Dict,
    ) -> Dict[str, str]:
        tables = erd.get("tables", [])
        mapped: Dict[str, str] = {}
        used_tables: Set[str] = set()

        for alias, required_cols in alias_columns.items():
            best_table = None
            best_score = -1
            for table in tables:
                table_name = table.get("name")
                if not table_name or table_name in used_tables:
                    continue
                table_cols = {c.get("name") for c in table.get("columns", [])}
                score = len(required_cols & table_cols)
                if required_cols.issubset(table_cols):
                    score += 100
                if score > best_score:
                    best_score = score
                    best_table = table_name
            if best_table and best_score > 0:
                mapped[alias] = best_table
                used_tables.add(best_table)

        return mapped

    def _build_from_join_clause(
        self,
        schema_name: str,
        alias_to_table: Dict[str, str],
        erd: Dict,
    ) -> str:
        aliases = list(alias_to_table.keys())
        if not aliases:
            return ""

        table_index = {table.get("name"): table for table in erd.get("tables", [])}
        first_alias = aliases[0]
        first_table = alias_to_table[first_alias]
        lines = [f'FROM "{schema_name}"."{first_table}" {first_alias}']
        connected_aliases = {first_alias}

        for alias in aliases[1:]:
            table_name = alias_to_table[alias]
            join_line = self._find_join_clause(
                schema_name=schema_name,
                alias=alias,
                table_name=table_name,
                connected_aliases=connected_aliases,
                alias_to_table=alias_to_table,
                table_index=table_index,
            )
            if not join_line:
                lines.append(f'CROSS JOIN "{schema_name}"."{table_name}" {alias}')
            else:
                lines.append(join_line)
            connected_aliases.add(alias)

        return "\n".join(lines)

    def _find_join_clause(
        self,
        schema_name: str,
        alias: str,
        table_name: str,
        connected_aliases: Set[str],
        alias_to_table: Dict[str, str],
        table_index: Dict[str, Dict],
    ) -> Optional[str]:
        this_table = table_index.get(table_name, {})
        this_fks = this_table.get("foreign_keys") or []

        for existing_alias in connected_aliases:
            existing_table = alias_to_table[existing_alias]
            existing_table_meta = table_index.get(existing_table, {})
            existing_fks = existing_table_meta.get("foreign_keys") or []

            for fk in this_fks:
                ref = fk.get("references") or {}
                if ref.get("table") == existing_table:
                    col = fk.get("column")
                    ref_col = ref.get("column")
                    if not col or not ref_col:
                        continue
                    return (
                        f'JOIN "{schema_name}"."{table_name}" {alias} '
                        f"ON {alias}.\"{col}\" = {existing_alias}.\"{ref_col}\""
                    )

            for fk in existing_fks:
                ref = fk.get("references") or {}
                if ref.get("table") == table_name:
                    col = fk.get("column")
                    ref_col = ref.get("column")
                    if not col or not ref_col:
                        continue
                    return (
                        f'JOIN "{schema_name}"."{table_name}" {alias} '
                        f"ON {existing_alias}.\"{col}\" = {alias}.\"{ref_col}\""
                    )

        # Fall back: no FK edge found.
        return None

    def _llm_repair(self, sql_query: str, error: str, erd_by_schema: Dict[str, Dict]) -> str:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "Repair this PostgreSQL SQL query using the schema metadata. "
                        "Return SQL only, no markdown or explanation."
                    ),
                ),
                ("human", "Error: {error}\nSchema metadata: {schema}\nSQL:\n{sql}"),
            ]
        )
        try:
            response = (prompt | self.llm).invoke(
                {"error": error, "schema": json.dumps(erd_by_schema), "sql": sql_query}
            )
        except Exception:
            return sql_query
        content = str(response.content).strip()
        return content

    def _ensure_group_by_for_aggregates(self, sql_query: str) -> str:
        try:
            expression = parse_one(sql_query, read="postgres")
        except ParseError:
            return sql_query

        has_agg = expression.find(exp.AggFunc) is not None
        has_group = expression.find(exp.Group) is not None
        if not has_agg or has_group:
            return sql_query

        select_node = expression.find(exp.Select)
        if not select_node:
            return sql_query

        group_columns: List[str] = []
        for proj in select_node.expressions:
            if proj.find(exp.AggFunc) is not None:
                continue
            candidate = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(candidate, exp.Column):
                group_columns.append(candidate.sql(dialect="postgres"))

        if not group_columns:
            return sql_query

        group_clause = "GROUP BY " + ", ".join(group_columns)
        pattern = re.compile(r"\b(ORDER\s+BY|LIMIT)\b", flags=re.IGNORECASE)
        match = pattern.search(sql_query)
        if not match:
            return f"{sql_query}\n{group_clause}"
        idx = match.start()
        return f"{sql_query[:idx].rstrip()}\n{group_clause}\n{sql_query[idx:].lstrip()}"
