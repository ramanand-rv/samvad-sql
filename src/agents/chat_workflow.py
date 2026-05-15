from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from src.db import get_engine
from src.models import ChartSpec, ChatRequest, ChatResponse, WorkflowStep


@dataclass
class ChatRunResult:
    response: ChatResponse
    pending_payload: Optional[Dict[str, Any]] = None


class SchemaParserAgent:
    def parse(self, engine: Engine) -> Dict[str, List[str]]:
        query = text(
            """
            SELECT table_schema, table_name, column_name
            FROM information_schema.columns
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_schema, table_name, ordinal_position
            """
        )
        schema: Dict[str, List[str]] = {}
        with engine.connect() as conn:
            for row in conn.execute(query).mappings():
                table = f'{row["table_schema"]}.{row["table_name"]}'
                schema.setdefault(table, []).append(row["column_name"])
        return schema


class QueryInterpreterAgent:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm

    def interpret(self, user_message: str, schema: Dict[str, List[str]]) -> Tuple[List[str], str]:
        stripped = user_message.strip().rstrip(";")
        if self._looks_like_sql(stripped):
            return [stripped + ";"], "Input appears to be SQL; using it directly."

        if not self.llm:
            if schema:
                first_table = next(iter(schema.keys()))
                return [f'SELECT * FROM {first_table} LIMIT 25;'], (
                    "LLM is unavailable. Ran a safe preview query on the first available table."
                )
            return [], "LLM is unavailable and no tables were found to build a fallback query."

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "You are a PostgreSQL query planner. Convert the user request to SQL. "
                        "Return ONLY JSON with keys: sql_statements (array of SQL strings), rationale (string). "
                        "If data/schema creation is needed, include CREATE/INSERT statements before SELECT."
                    ),
                ),
                (
                    "human",
                    "Schema: {schema}\nUser request: {message}",
                ),
            ]
        )
        try:
            response = (prompt | self.llm).invoke({"schema": schema, "message": user_message})
            raw = str(response.content).strip()
        except Exception:
            raw = ""
        payload = self._parse_json(raw)
        sql_statements = [
            stmt.strip().rstrip(";") + ";"
            for stmt in payload.get("sql_statements", [])
            if isinstance(stmt, str) and stmt.strip()
        ]
        rationale = str(payload.get("rationale", "Generated SQL from natural-language request."))
        return sql_statements, rationale

    @staticmethod
    def _looks_like_sql(text_value: str) -> bool:
        starts = ("select", "insert", "update", "delete", "create", "alter", "drop", "truncate", "with")
        return text_value.lower().startswith(starts)

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    return {}
        return {}


class SQLValidatorAgent:
    def validate(self, sql_statements: List[str], schema: Dict[str, List[str]]) -> Tuple[bool, List[str], List[str]]:
        errors: List[str] = []
        missing_tables: List[str] = []
        known_tables = set(schema.keys()) | {name.split(".", 1)[-1] for name in schema.keys()}

        for stmt in sql_statements:
            try:
                expression = parse_one(stmt, read="postgres")
            except ParseError as exc:
                errors.append(f"Parse error in statement `{stmt}`: {exc}")
                continue

            for table_node in expression.find_all(exp.Table):
                if self._statement_creates_table(expression):
                    continue
                raw_name = table_node.sql(dialect="postgres").replace('"', "")
                simple_name = table_node.name
                if raw_name not in known_tables and simple_name not in known_tables:
                    missing_tables.append(raw_name)

        dedup_missing = sorted({table for table in missing_tables if table})
        return not errors, errors, dedup_missing

    @staticmethod
    def _statement_creates_table(expression: exp.Expression) -> bool:
        return isinstance(expression, (exp.Create, exp.Alter))


class DebuggerAgent:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm

    def try_fix(self, sql_statement: str, error_text: str, schema: Dict[str, List[str]]) -> str:
        if not self.llm:
            return sql_statement
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Fix this PostgreSQL query using the error and schema. Return SQL only, no explanation.",
                ),
                (
                    "human",
                    "Schema: {schema}\nError: {error}\nSQL: {sql}",
                ),
            ]
        )
        try:
            response = (prompt | self.llm).invoke(
                {"schema": schema, "error": error_text, "sql": sql_statement}
            )
            content = str(response.content).strip()
        except Exception:
            return sql_statement
        return content.rstrip(";") + ";"


class DataGenerationAgent:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm

    def propose_for_missing_tables(self, user_message: str, missing_tables: List[str]) -> List[str]:
        if not missing_tables:
            return []
        if self.llm:
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        (
                            "Generate PostgreSQL CREATE TABLE and INSERT statements for the missing tables. "
                            "Return SQL only, one statement per line. Use sensible PK/FK relations."
                        ),
                    ),
                    (
                        "human",
                        "User request: {message}\nMissing tables: {tables}",
                    ),
                ]
            )
            try:
                response = (prompt | self.llm).invoke({"message": user_message, "tables": missing_tables})
                statements = self._split_sql(str(response.content))
                if statements:
                    return statements
            except Exception:
                pass

        return self._fallback_missing_tables(missing_tables)

    @staticmethod
    def _split_sql(raw_sql: str) -> List[str]:
        lines = [line.strip() for line in raw_sql.splitlines() if line.strip()]
        statements: List[str] = []
        current = []
        for line in lines:
            current.append(line)
            if line.endswith(";"):
                statements.append(" ".join(current))
                current = []
        if current:
            statements.append(" ".join(current) + ";")
        filtered = [stmt for stmt in statements if stmt.lower().startswith(("create", "insert", "alter"))]
        return filtered

    @staticmethod
    def _fallback_missing_tables(missing_tables: List[str]) -> List[str]:
        statements: List[str] = []
        for table in missing_tables:
            simple = table.split(".")[-1]
            if simple == "customers":
                statements.extend(
                    [
                        "CREATE TABLE IF NOT EXISTS customers (id SERIAL PRIMARY KEY, name TEXT, email TEXT);",
                        "INSERT INTO customers (name, email) VALUES ('Alice', 'alice@example.com'), ('Bob', 'bob@example.com');",
                    ]
                )
            elif simple == "orders":
                statements.extend(
                    [
                        "CREATE TABLE IF NOT EXISTS orders (id SERIAL PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), amount NUMERIC(10,2), status TEXT, created_at TIMESTAMP DEFAULT NOW());",
                        "INSERT INTO orders (customer_id, amount, status) VALUES (1, 120.00, 'paid'), (2, 40.00, 'pending');",
                    ]
                )
            else:
                statements.append(
                    f'CREATE TABLE IF NOT EXISTS "{simple}" (id SERIAL PRIMARY KEY, name TEXT);'
                )
        return statements


class ChatExecutorAgent:
    def execute(self, engine: Engine, sql_statements: List[str], preview_limit: int) -> Tuple[List[str], List[Dict[str, Any]], str]:
        rows: List[Dict[str, Any]] = []
        columns: List[str] = []
        execution_note = "Executed successfully."

        with engine.begin() as conn:
            for stmt in sql_statements:
                result = conn.execute(text(stmt))
                if result.returns_rows:
                    mapped = result.mappings().fetchmany(preview_limit)
                    rows = [dict(r) for r in mapped]
                    columns = list(rows[0].keys()) if rows else []
                    execution_note = f"Fetched {len(rows)} rows (preview)."
                else:
                    execution_note = f"Statement affected {int(result.rowcount or 0)} rows."
        return columns, rows, execution_note


class ChatWorkflow:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm
        self.schema_parser = SchemaParserAgent()
        self.interpreter = QueryInterpreterAgent(llm=llm)
        self.validator = SQLValidatorAgent()
        self.debugger = DebuggerAgent(llm=llm)
        self.data_generator = DataGenerationAgent(llm=llm)
        self.executor = ChatExecutorAgent()

    def run(self, request: ChatRequest) -> ChatRunResult:
        steps: List[WorkflowStep] = []
        if not request.message.strip():
            return ChatRunResult(
                response=ChatResponse(status="error", message="Message cannot be empty.")
            )

        try:
            engine = get_engine()
        except Exception as exc:
            return ChatRunResult(
                response=ChatResponse(
                    status="error",
                    message=f"Database not configured: {exc}",
                )
            )

        steps.append(WorkflowStep(icon="🔍", message="Parsing schema"))
        try:
            schema = self.schema_parser.parse(engine)
        except Exception as exc:
            return ChatRunResult(
                response=ChatResponse(
                    status="error",
                    message=self._friendly_db_error(exc),
                    steps=steps,
                )
            )

        steps.append(WorkflowStep(icon="🧠", message="Interpreting user request"))
        sql_statements, rationale = self.interpreter.interpret(request.message, schema)
        if not sql_statements:
            return ChatRunResult(
                response=ChatResponse(
                    status="error",
                    message=f"Unable to generate SQL. {rationale}",
                    steps=steps,
                )
            )

        steps.append(WorkflowStep(icon="📝", message="Validating SQL"))
        is_valid, errors, missing_tables = self.validator.validate(sql_statements, schema)
        if not is_valid and errors:
            fixed_sql = sql_statements[:]
            for attempt in range(3):
                fixed_sql[0] = self.debugger.try_fix(fixed_sql[0], errors[0], schema)
                valid_after_fix, new_errors, new_missing = self.validator.validate(fixed_sql, schema)
                if valid_after_fix:
                    sql_statements = fixed_sql
                    missing_tables = new_missing
                    break
                errors = new_errors
                steps.append(WorkflowStep(icon="🛠️", message=f"Debugging SQL attempt {attempt + 1}"))
            else:
                return ChatRunResult(
                    response=ChatResponse(
                        status="error",
                        message=f"SQL validation failed: {errors[0]}",
                        sql=sql_statements,
                        steps=steps,
                    )
                )

        if missing_tables:
            if not request.auto_generate_missing_data:
                return ChatRunResult(
                    response=ChatResponse(
                        status="error",
                        message=f"Missing required tables: {', '.join(missing_tables)}.",
                        sql=sql_statements,
                        steps=steps,
                    )
                )
            proposal = self.data_generator.propose_for_missing_tables(request.message, missing_tables)
            proposed_bundle = proposal + sql_statements
            steps.append(WorkflowStep(icon="⚠️", message="Approval needed for schema/data creation"))
            return ChatRunResult(
                response=ChatResponse(
                    status="approval_needed",
                    message="Approval required to create missing schema/data before running your request.",
                    sql=proposed_bundle,
                    approval_reason=(
                        f"Missing tables detected ({', '.join(missing_tables)}). "
                        "The system proposes CREATE/INSERT statements."
                    ),
                    steps=steps,
                ),
                pending_payload={
                    "kind": "chat_sql_bundle",
                    "sql_statements": proposed_bundle,
                    "preview_limit": request.preview_limit,
                },
            )

        risky = [stmt for stmt in sql_statements if not stmt.strip().lower().startswith("select")]
        if risky:
            steps.append(WorkflowStep(icon="⚠️", message="Approval needed for data/schema modification"))
            return ChatRunResult(
                response=ChatResponse(
                    status="approval_needed",
                    message="Approval required before running modifying SQL statements.",
                    sql=sql_statements,
                    approval_reason="One or more statements can modify schema/data.",
                    steps=steps,
                ),
                pending_payload={
                    "kind": "chat_sql_bundle",
                    "sql_statements": sql_statements,
                    "preview_limit": request.preview_limit,
                },
            )

        execution_response = self.execute_sql_bundle(
            sql_statements=sql_statements,
            preview_limit=request.preview_limit,
            pre_steps=steps,
            rationale=rationale,
        )
        return ChatRunResult(response=execution_response)

    def execute_sql_bundle(
        self,
        sql_statements: List[str],
        preview_limit: int,
        pre_steps: Optional[List[WorkflowStep]] = None,
        rationale: str | None = None,
    ) -> ChatResponse:
        steps = list(pre_steps or [])
        try:
            engine = get_engine()
        except Exception as exc:
            return ChatResponse(status="error", message=f"Database not configured: {exc}", steps=steps, sql=sql_statements)

        steps.append(WorkflowStep(icon="📊", message="Executing SQL"))
        try:
            columns, rows, note = self.executor.execute(engine, sql_statements, preview_limit)
        except Exception as exc:
            return ChatResponse(
                status="error",
                message=f"Execution failed: {exc}",
                steps=steps,
                sql=sql_statements,
            )

        steps.append(WorkflowStep(icon="📈", message="Preparing result visualization"))
        chart = suggest_chart(rows, columns)
        message = note if not rationale else f"{note} {rationale}"
        return ChatResponse(
            status="success",
            message=message.strip(),
            steps=steps,
            sql=sql_statements,
            columns=columns,
            rows=rows,
            chart=chart,
        )

    @staticmethod
    def _friendly_db_error(exc: Exception) -> str:
        error_text = str(exc)
        if "password authentication failed" in error_text.lower():
            return (
                "Failed to connect to PostgreSQL: authentication failed. "
                "Please verify POSTGRES_USER / POSTGRES_PASSWORD (or DATABASE_URL), "
                "and confirm the same credentials work in psql/pgAdmin."
            )
        return (
            f"Failed to read schema from PostgreSQL: {error_text}. "
            "Check host, port, database name, and network reachability."
        )


def suggest_chart(rows: List[Dict[str, Any]], columns: List[str]) -> ChartSpec:
    if not rows or not columns:
        return ChartSpec(chart_type="table")

    numeric_cols = []
    for col in columns:
        first = rows[0].get(col)
        if isinstance(first, (int, float)):
            numeric_cols.append(col)

    if len(columns) >= 2 and numeric_cols:
        x_col = next((c for c in columns if c not in numeric_cols), columns[0])
        y_col = numeric_cols[0]
        return ChartSpec(chart_type="bar", x=x_col, y=y_col, title=f"{y_col} by {x_col}")

    if len(numeric_cols) == 1:
        return ChartSpec(chart_type="line", x=columns[0], y=numeric_cols[0], title=f"{numeric_cols[0]} trend")

    return ChartSpec(chart_type="table")
