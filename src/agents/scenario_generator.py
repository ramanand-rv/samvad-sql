from __future__ import annotations

from typing import List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from src.models import QueryAnalysis, ScenarioDefinition, ScenarioPriority


class ScenarioGenerator:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm

    def generate_scenarios(
        self,
        analysis: QueryAnalysis,
        max_scenarios: int = 8,
        include_performance: bool = False,
        positive_cases: int = 3,
        negative_cases: int = 2,
        edge_cases: int = 1,
    ) -> List[ScenarioDefinition]:
        scenarios: List[ScenarioDefinition] = []
        scenario_index = 1
        primary_table = self._primary_table_name(analysis.tables)
        sample_column = self._sample_column_name(analysis.columns)

        for idx in range(max(0, positive_cases)):
            title = self._positive_title(analysis, primary_table, sample_column, idx)
            description = self._positive_description(analysis, primary_table, sample_column, idx)
            scenarios.append(
                ScenarioDefinition(
                    scenario_id=f"S{scenario_index}",
                    name=title,
                    description=description,
                    priority=ScenarioPriority.critical,
                    expectation="execution_success",
                    tags=["positive", "baseline"],
                )
            )
            scenario_index += 1

        for idx in range(max(0, negative_cases)):
            title = self._negative_title(analysis, primary_table, sample_column, idx)
            description = self._negative_description(analysis, primary_table, sample_column, idx)
            scenarios.append(
                ScenarioDefinition(
                    scenario_id=f"S{scenario_index}",
                    name=title,
                    description=description,
                    priority=ScenarioPriority.critical,
                    expectation="row_count_eq_zero",
                    tags=["negative", "filters", "empty_result"],
                )
            )
            scenario_index += 1

        edge_blueprints = [
            (
                f"Validate NULL handling in {primary_table}",
                f"Inject NULLs into query-critical columns such as {sample_column} to validate SQL NULL semantics.",
                ["edge", "nulls"],
            ),
            (
                f"Validate boundary comparisons for {sample_column}",
                "Create rows exactly at, below, and above boundary values used by filter logic.",
                ["edge", "boundary"],
            ),
            (
                f"Verify duplicate active assignments are handled in {primary_table}",
                "Create duplicate active rows to confirm deduplication and grouping behavior remains correct.",
                ["edge", "duplicates"],
            ),
            (
                f"Validate empty-table behavior for {primary_table}",
                "Execute query against structurally valid but empty tables and confirm safe behavior.",
                ["edge", "empty_table"],
            ),
        ]
        for idx in range(max(0, edge_cases)):
            name, description, tags = edge_blueprints[idx % len(edge_blueprints)]
            scenarios.append(
                ScenarioDefinition(
                    scenario_id=f"S{scenario_index}",
                    name=f"{name} {idx + 1}",
                    description=description,
                    priority=ScenarioPriority.normal,
                    expectation="execution_success",
                    tags=tags,
                )
            )
            scenario_index += 1

        if analysis.group_by or analysis.aggregations:
            scenarios.append(
                ScenarioDefinition(
                    scenario_id=f"S{scenario_index}",
                    name=f"Ensure {', '.join(analysis.aggregations) or 'aggregation'} correctness for grouped output",
                    description="Create grouped rows with known totals and counts to verify aggregation behavior.",
                    priority=ScenarioPriority.critical,
                    expectation="execution_success",
                    tags=["aggregation", "group_by", "edge"],
                )
            )
            scenario_index += 1

        if analysis.tables and len(analysis.tables) > 1:
            scenarios.append(
                ScenarioDefinition(
                    scenario_id=f"S{scenario_index}",
                    name=f"Check join cardinality across {len(analysis.tables)} related tables",
                    description="Create matching and non-matching relationship rows to validate join behavior.",
                    priority=ScenarioPriority.critical,
                    expectation="execution_success",
                    tags=["joins", "edge"],
                )
            )
            scenario_index += 1

        if include_performance:
            scenarios.append(
                ScenarioDefinition(
                    scenario_id=f"S{scenario_index}",
                    name=f"Run performance smoke test on {primary_table}",
                    description="Populate larger data volume and assert query executes within acceptable threshold.",
                    priority=ScenarioPriority.performance,
                    expectation="execution_success",
                    tags=["performance"],
                )
            )
            scenario_index += 1

        scenarios.extend(self._llm_suggestions(analysis, scenario_index))

        # Deduplicate by name and enforce max_scenarios
        deduped: List[ScenarioDefinition] = []
        seen = set()
        for scenario in scenarios:
            key = scenario.name.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(scenario)
            if len(deduped) >= max_scenarios:
                break

        return deduped

    @staticmethod
    def _primary_table_name(tables: List[str]) -> str:
        if not tables:
            return "target table"
        return tables[0]

    @staticmethod
    def _sample_column_name(columns: List[str]) -> str:
        if not columns:
            return "key columns"
        first = columns[0]
        return first.split(".")[-1].replace('"', "")

    @staticmethod
    def _positive_title(
        analysis: QueryAnalysis,
        primary_table: str,
        sample_column: str,
        idx: int,
    ) -> str:
        suffix = f" (case {idx + 1})"
        if analysis.group_by and analysis.aggregations:
            return (
                f"Validate grouped {', '.join(analysis.aggregations)} output for {primary_table} "
                f"using realistic {sample_column} values{suffix}"
            )
        if len(analysis.tables) > 1:
            return f"Validate referential consistency across joined tables in {primary_table}{suffix}"
        return f"Validate valid business rows are returned from {primary_table}{suffix}"

    @staticmethod
    def _positive_description(
        analysis: QueryAnalysis,
        primary_table: str,
        sample_column: str,
        idx: int,
    ) -> str:
        if analysis.conditions:
            return (
                f"Populate rows that satisfy filter predicates and validate that {sample_column} participates "
                f"correctly in expected output."
            )
        return (
            f"Populate representative rows for {primary_table} and verify that the query returns meaningful "
            "non-empty results."
        )

    @staticmethod
    def _negative_title(
        analysis: QueryAnalysis,
        primary_table: str,
        sample_column: str,
        idx: int,
    ) -> str:
        suffix = f" (case {idx + 1})"
        if analysis.conditions:
            return f"Validate exclusion when predicates are not met for {sample_column}{suffix}"
        if analysis.group_by:
            return f"Ensure grouped output is empty when no qualifying rows exist in {primary_table}{suffix}"
        return f"Validate query returns no rows for non-matching dataset in {primary_table}{suffix}"

    @staticmethod
    def _negative_description(
        analysis: QueryAnalysis,
        primary_table: str,
        sample_column: str,
        idx: int,
    ) -> str:
        if analysis.conditions:
            return (
                "Populate records intentionally violating WHERE/HAVING conditions so result should be empty."
            )
        return "Populate unrelated rows and confirm the query safely returns zero rows."

    def _llm_suggestions(self, analysis: QueryAnalysis, next_id: int) -> List[ScenarioDefinition]:
        if not self.llm:
            return []

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Suggest up to two additional SQL test scenarios. Return one scenario per line as: name | description",
                ),
                ("human", "Query analysis: {analysis}"),
            ]
        )
        try:
            response = (prompt | self.llm).invoke({"analysis": analysis.model_dump()})
        except Exception:
            return []
        text = str(response.content)

        scenarios: List[ScenarioDefinition] = []
        for line in text.splitlines():
            parts = [segment.strip() for segment in line.split("|", 1)]
            if len(parts) != 2:
                continue
            scenarios.append(
                ScenarioDefinition(
                    scenario_id=f"S{next_id}",
                    name=parts[0][:80],
                    description=parts[1][:300],
                    priority=ScenarioPriority.normal,
                    expectation="execution_success",
                    tags=["llm"],
                )
            )
            next_id += 1
            if len(scenarios) == 2:
                break
        return scenarios
