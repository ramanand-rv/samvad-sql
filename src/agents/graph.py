from __future__ import annotations

from typing import Dict, List, Optional, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from src.agents.data_auditor import DataAuditor
from src.agents.data_populator import DataPopulator
from src.agents.query_analyzer import QueryAnalyzer
from src.agents.report_builder import ReportBuilder
from src.agents.safety import SafetyAgent
from src.agents.scenario_generator import ScenarioGenerator
from src.agents.sql_repair_agent import SQLRepairAgent
from src.agents.test_executor import TestExecutor
from src.config import settings
from src.db import PostgresTempDBManager
from src.models import (
    QueryAnalysis,
    SQLTestReport,
    ScenarioExecutionResult,
    ScenarioDefinition,
    ScenarioStatus,
    TestQueryRequest,
    TestQueryResponse,
)
from src.logging_config import get_logger

logger = get_logger(__name__)


class GraphState(TypedDict, total=False):
    request: TestQueryRequest
    effective_sql: str
    repair_notes: List[str]
    analysis: QueryAnalysis
    scenarios: List[ScenarioDefinition]
    schema: Dict[str, List[str]]
    results: List[ScenarioExecutionResult]
    requires_approval: bool
    approval_reason: str
    report: SQLTestReport


class SQLTestingGraph:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm
        self.query_analyzer = QueryAnalyzer(llm=llm)
        self.safety_agent = SafetyAgent()
        self.scenario_generator = ScenarioGenerator(llm=llm)
        self.sql_repair_agent = SQLRepairAgent(llm=llm)
        self.data_auditor = DataAuditor(llm=llm)
        self.data_populator = DataPopulator(llm=llm)
        self.db_manager = PostgresTempDBManager(template_db=settings.test_db_template)
        self.test_executor = TestExecutor(llm=llm, db_manager=self.db_manager)
        self.report_builder = ReportBuilder()
        self.graph = self._build_graph()
        logger.info("SQLTestingGraph initialized (max_scenarios=%s)", settings.max_scenarios)

    def _build_graph(self):
        builder = StateGraph(GraphState)
        builder.add_node("analyze_query", self.analyze_query_node)
        builder.add_node("generate_scenarios", self.generate_scenarios_node)
        builder.add_node("safety_gate", self.safety_gate_node)
        builder.add_node("execute_scenarios", self.execute_scenarios_node)
        builder.add_node("build_report", self.build_report_node)
        builder.add_node("approval_pending", self.approval_pending_node)

        builder.add_edge(START, "analyze_query")
        builder.add_edge("analyze_query", "generate_scenarios")
        builder.add_edge("generate_scenarios", "safety_gate")
        builder.add_conditional_edges(
            "safety_gate",
            self.route_after_safety,
            {
                "approval_pending": "approval_pending",
                "continue": "execute_scenarios",
            },
        )
        builder.add_edge("execute_scenarios", "build_report")
        builder.add_edge("build_report", END)
        builder.add_edge("approval_pending", END)
        return builder.compile()

    def analyze_query_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        effective_sql = request.sql_query
        analysis = self.query_analyzer.analyze(effective_sql)
        repair_notes: List[str] = []

        if settings.has_database and (analysis.parse_error or not analysis.tables):
            try:
                erd_by_schema: Dict[str, Dict] = {}
                for schema_name in self.db_manager.list_user_schemas():
                    erd_by_schema[schema_name] = self.db_manager.get_erd_metadata(schema_name)
                repaired_sql, notes = self.sql_repair_agent.repair_for_testing(
                    sql_query=effective_sql,
                    analysis=analysis,
                    erd_by_schema=erd_by_schema,
                )
                if repaired_sql.strip() and repaired_sql.strip() != effective_sql.strip():
                    effective_sql = repaired_sql
                    repair_notes.extend(notes)
                    analysis = self.query_analyzer.analyze(effective_sql)
            except Exception as exc:
                repair_notes.append(f"SQL auto-repair skipped: {exc}")
                logger.exception("Auto-repair skipped due to error")

        logger.debug("analyze_query_node: query_type=%s tables=%s", analysis.query_type, analysis.tables)
        return {"analysis": analysis, "effective_sql": effective_sql, "repair_notes": repair_notes}

    def generate_scenarios_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        analysis = state["analysis"]
        scenarios = self.scenario_generator.generate_scenarios(
            analysis=analysis,
            max_scenarios=min(request.max_scenarios, settings.max_scenarios),
            include_performance=request.include_performance_scenario,
            positive_cases=request.positive_cases,
            negative_cases=request.negative_cases,
            edge_cases=request.edge_cases,
        )
        schema: Dict[str, List[str]] = {}
        if analysis.tables:
            try:
                logger.debug("Loading table schema for analysis tables: %s", analysis.tables)
                schema = self.db_manager.get_table_schema(analysis.tables)
            except Exception:
                logger.exception("Failed to load schema for analysis tables")
                schema = {}
        return {"scenarios": scenarios, "schema": schema}

    def safety_gate_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        analysis = state["analysis"]
        safety = self.safety_agent.assess(analysis)

        requires_approval = safety.requires_approval
        approval_reason = " ".join(safety.reasons).strip()

        if requires_approval:
            approved = self.safety_agent.is_approved(
                request.approval_token,
                settings.normalized_approval_tokens,
            )
            if approved or settings.allow_destructive_without_approval:
                requires_approval = False
                approval_reason = ""

        return {
            "requires_approval": requires_approval,
            "approval_reason": approval_reason,
        }

    def route_after_safety(self, state: GraphState) -> str:
        if state.get("requires_approval"):
            return "approval_pending"
        return "continue"

    def execute_scenarios_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        analysis = state["analysis"]
        scenarios = state.get("scenarios", [])
        schema = state.get("schema", {})
        effective_sql = state.get("effective_sql", request.sql_query)

        if analysis.parse_error:
            results = [
                ScenarioExecutionResult(
                    scenario_id="PARSE",
                    name="SQL parsing",
                    status=ScenarioStatus.failed,
                    reason=analysis.parse_error,
                )
            ]
            return {"results": results}

        if not scenarios:
            return {"results": []}

        if not settings.has_database:
            results = [
                ScenarioExecutionResult(
                    scenario_id=scenario.scenario_id,
                    name=scenario.name,
                    status=ScenarioStatus.skipped,
                    reason=(
                        "PostgreSQL is not configured. Set DATABASE_URL or POSTGRES_* environment "
                        "variables and TEST_DB_TEMPLATE."
                    ),
                )
                for scenario in scenarios
            ]
            logger.warning("Database not configured; skipping execution of %d scenarios", len(scenarios))
            return {"results": results}

        results: List[ScenarioExecutionResult] = []
        for scenario in scenarios:
            scenario_payload = {
                "name": scenario.name,
                "description": scenario.description,
                "scenario_id": scenario.scenario_id,
                "expectation": scenario.expectation,
            }

            inserts: List[str] = []
            if request.generate_missing_data and self.data_auditor.scenario_needs_data(scenario_payload):
                inserts = self.data_populator.generate_data(
                    scenario=scenario_payload,
                    tables=analysis.tables,
                    schema=schema,
                )

            outcome = self.test_executor.run_scenario(
                scenario=scenario_payload,
                user_sql=effective_sql,
                insert_statements=inserts,
            )
            results.append(
                ScenarioExecutionResult(
                    scenario_id=scenario.scenario_id,
                    name=scenario.name,
                    status=ScenarioStatus.passed if outcome["passed"] else ScenarioStatus.failed,
                    reason=outcome["failure_reason"],
                    isolation_mode=outcome.get("isolation_mode"),
                    execution_time_ms=outcome["execution_time_ms"],
                    row_count=len(outcome["actual_results"]),
                    sample_rows=outcome["actual_results"][:20],
                    expected_rows=outcome["expected_results"][:20],
                    generated_rows=len(inserts),
                )
            )
        return {"results": results}

    def build_report_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        effective_sql = state.get("effective_sql", request.sql_query)
        report = SQLTestReport(
            sql_query=effective_sql,
            analysis=state["analysis"],
            scenarios=state.get("scenarios", []),
            results=state.get("results", []),
        )

        if not settings.has_database:
            report.notes.append(
                "PostgreSQL is not configured. Results are generated without execution."
            )

        report = self.report_builder.summarize(report)
        if state.get("repair_notes"):
            report.notes.extend(state["repair_notes"])
        if effective_sql.strip() != request.sql_query.strip():
            report.notes.append("Query was auto-repaired before test execution.")
        return {"report": report}

    def approval_pending_node(self, state: GraphState) -> GraphState:
        # No-op node used to end execution with approval requirement details.
        return state

    def run(self, request: TestQueryRequest) -> TestQueryResponse:
        final_state = self.graph.invoke({"request": request})

        if final_state.get("requires_approval"):
            return TestQueryResponse(
                status="awaiting_approval",
                requires_approval=True,
                approval_reason=final_state.get("approval_reason") or "Approval required.",
                scenario_preview=final_state.get("scenarios", []),
            )

        return TestQueryResponse(
            status="success",
            report=final_state.get("report"),
        )
