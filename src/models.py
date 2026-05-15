import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class QueryRiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ScenarioStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


class ScenarioPriority(str, Enum):
    critical = "critical"
    normal = "normal"
    performance = "performance"


class QueryAnalysis(BaseModel):
    query_type: str
    tables: List[str] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    conditions: List[str] = Field(default_factory=list)
    aggregations: List[str] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    order_by: List[str] = Field(default_factory=list)
    limit: Optional[int] = None
    is_destructive: bool = False
    has_where: bool = False
    parse_error: Optional[str] = None


class ScenarioDefinition(BaseModel):
    scenario_id: str
    name: str
    description: str
    priority: ScenarioPriority = ScenarioPriority.normal
    expectation: str
    tags: List[str] = Field(default_factory=list)


class ScenarioExecutionResult(BaseModel):
    scenario_id: str
    name: str
    status: ScenarioStatus
    reason: Optional[str] = None
    isolation_mode: Optional[str] = None
    execution_time_ms: float = 0.0
    row_count: Optional[int] = None
    sample_rows: List[Dict[str, Any]] = Field(default_factory=list)
    expected_rows: List[Dict[str, Any]] = Field(default_factory=list)
    generated_rows: int = 0


class SQLTestReport(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sql_query: str
    analysis: QueryAnalysis
    scenarios: List[ScenarioDefinition] = Field(default_factory=list)
    results: List[ScenarioExecutionResult] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class TestQueryRequest(BaseModel):
    sql_query: str = Field(min_length=1)
    generate_missing_data: bool = True
    include_performance_scenario: bool = False
    positive_cases: int = Field(default=3, ge=0, le=20)
    negative_cases: int = Field(default=2, ge=0, le=20)
    edge_cases: int = Field(default=1, ge=0, le=20)
    max_scenarios: int = Field(default=8, ge=1, le=20)
    approval_token: Optional[str] = None


class TestQueryResponse(BaseModel):
    status: str
    requires_approval: bool = False
    approval_reason: Optional[str] = None
    approval_request_id: Optional[str] = None
    report: Optional[SQLTestReport] = None
    scenario_preview: List[ScenarioDefinition] = Field(default_factory=list)


class WorkflowStep(BaseModel):
    icon: str
    message: str


class ChartSpec(BaseModel):
    chart_type: Literal["bar", "line", "pie", "table"] = "table"
    x: Optional[str] = None
    y: Optional[str] = None
    title: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    auto_generate_missing_data: bool = True
    preview_limit: int = Field(default=200, ge=1, le=2000)


class ChatResponse(BaseModel):
    status: Literal["success", "approval_needed", "error"]
    message: str
    steps: List[WorkflowStep] = Field(default_factory=list)
    sql: List[str] = Field(default_factory=list)
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    chart: Optional[ChartSpec] = None
    approval_request_id: Optional[str] = None
    approval_reason: Optional[str] = None


class ApprovalRequest(BaseModel):
    request_id: str
    approved: bool


class ApprovalResponse(BaseModel):
    status: Literal["success", "cancelled", "not_found", "error"]
    message: str
    test_response: Optional[TestQueryResponse] = None
    chat_response: Optional[ChatResponse] = None
