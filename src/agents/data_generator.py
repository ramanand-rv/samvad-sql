from __future__ import annotations

from typing import Dict, List, Optional

from src.db.introspection import ColumnInfo
from src.models import QueryAnalysis, ScenarioDefinition


class DataGenerator:
    """Generates deterministic scenario data from schema metadata."""

    def generate_rows(
        self,
        schema_snapshot: Dict[str, List[ColumnInfo]],
        scenario: ScenarioDefinition,
        analysis: QueryAnalysis,
    ) -> Dict[str, List[dict]]:
        rows_by_table: Dict[str, List[dict]] = {}
        row_count = 3
        if "performance" in scenario.tags:
            row_count = 200

        for table_name, columns in schema_snapshot.items():
            table_rows: List[dict] = []
            for idx in range(row_count):
                row: dict = {}
                for column in columns:
                    value = self._value_for_column(column, scenario, idx, analysis)
                    if value is not _SKIP:
                        row[column.name] = value
                if row:
                    table_rows.append(row)
            rows_by_table[table_name] = table_rows

        return rows_by_table
 
    def _value_for_column(
        self,
        column: ColumnInfo,
        scenario: ScenarioDefinition,
        row_index: int,
        analysis: QueryAnalysis,
    ):
        # Use DB defaults if available.
        if column.column_default:
            return _SKIP

        name = column.name.lower()
        dtype = column.data_type.lower()

        wants_nulls = "null" in scenario.name.lower() or "nulls" in scenario.tags
        if wants_nulls and column.is_nullable and row_index == 0 and name in self._candidate_nullable_columns(analysis):
            return None

        if dtype in {"integer", "bigint", "smallint"}:
            return self._integer_value(name, scenario, row_index)

        if dtype in {"numeric", "decimal", "real", "double precision"}:
            return self._decimal_value(scenario, row_index)

        if "timestamp" in dtype:
            return f"2026-01-{min(row_index + 1, 28):02d} 10:00:00"

        if dtype == "date":
            return f"2026-01-{min(row_index + 1, 28):02d}"

        if dtype in {"boolean"}:
            return row_index % 2 == 0

        if dtype in {"json", "jsonb"}:
            return "{}"

        if "char" in dtype or dtype in {"text", "uuid"}:
            return self._text_value(name, scenario, row_index)

        # Conservative fallback for unknown data types.
        return self._text_value(name, scenario, row_index)

    @staticmethod
    def _candidate_nullable_columns(analysis: QueryAnalysis) -> set[str]:
        candidates = set()
        for column in analysis.columns:
            plain = column.split(".")[-1].replace('"', "")
            candidates.add(plain.lower())
        return candidates

    @staticmethod
    def _integer_value(column_name: str, scenario: ScenarioDefinition, row_index: int) -> int:
        scenario_name = scenario.name.lower()

        if "boundary" in scenario_name:
            boundary_values = [99, 100, 101]
            return boundary_values[row_index % len(boundary_values)]

        if "no matching" in scenario_name:
            return -1000 - row_index

        if "aggregation" in scenario_name and (column_name.endswith("_id") or column_name == "id"):
            return 1 if row_index < 2 else 2

        if "join" in scenario_name and (column_name.endswith("_id") or column_name == "id"):
            return row_index + 1

        return row_index + 1

    @staticmethod
    def _decimal_value(scenario: ScenarioDefinition, row_index: int) -> float:
        scenario_name = scenario.name.lower()

        if "boundary" in scenario_name:
            boundary_values = [99.0, 100.0, 101.0]
            return boundary_values[row_index % len(boundary_values)]

        if "no matching" in scenario_name:
            return -10.0 - float(row_index)

        if "aggregation" in scenario_name:
            values = [60.0, 50.0, 10.0]
            return values[row_index % len(values)]

        return [25.5, 75.0, 150.25][row_index % 3]

    @staticmethod
    def _text_value(column_name: str, scenario: ScenarioDefinition, row_index: int) -> str:
        scenario_name = scenario.name.lower()

        if "status" in column_name:
            if "no matching" in scenario_name:
                return "non_matching"
            if "null" in scenario_name and row_index == 1:
                return "paid"
            return ["paid", "pending", "cancelled"][row_index % 3]

        if "no matching" in scenario_name:
            return f"no_match_{column_name}_{row_index + 1}"

        return f"{column_name}_{row_index + 1}"


class _SkipValue:
    pass


_SKIP = _SkipValue()
