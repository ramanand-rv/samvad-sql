import uuid
from contextlib import asynccontextmanager
from threading import Lock
from typing import Any, Dict

from fastapi import FastAPI, HTTPException

from src.agents.chat_workflow import ChatWorkflow
from src.agents.graph import SQLTestingGraph
from src.config import settings
from src.llm_factory import get_llm
from src.models import (
    ApprovalRequest,
    ApprovalResponse,
    ChatRequest,
    ChatResponse,
    TestQueryRequest,
    TestQueryResponse,
)
from src.logging_config import get_logger, redact_db_url

logger = get_logger(__name__)

BANNER = r"""
 ▄▄▄▄▄▄▄                               ▄▄    ▄▄▄▄▄▄▄   ▄▄▄▄▄   ▄▄▄      
█████▀▀▀                               ██   █████▀▀▀ ▄███████▄ ███      
 ▀████▄   ▀▀█▄ ███▄███▄ ██ ██  ▀▀█▄ ▄████    ▀████▄  ███   ███ ███      
   ▀████ ▄█▀██ ██ ██ ██ ██▄██ ▄█▀██ ██ ██      ▀████ ███▄█▄███ ███      
███████▀ ▀█▄██ ██ ██ ██  ▀█▀  ▀█▄██ ▀████   ███████▀  ▀█████▀  ████████ 
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s in %s mode", settings.app_name, settings.app_env)
    # Print ASCII banner on startup
    logger.info("\n%s", BANNER)
    # Helpful DB startup context
    try:
        logger.info(
            "Database configured=%s, DSN=%s",
            settings.has_database,
            redact_db_url(settings.database_url),
        )
    except Exception:
        logger.debug("Unable to introspect DB settings at startup.")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
llm = get_llm()
logger.info("LLM provider: %s (enabled=%s)", settings.llm_provider, bool(llm))
graph = SQLTestingGraph(llm=llm)
logger.info("SQLTestingGraph initialized")
chat_workflow = ChatWorkflow(llm=llm)
logger.info("ChatWorkflow initialized")
pending_actions: Dict[str, Dict[str, Any]] = {}
pending_lock = Lock()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "database_configured": settings.has_database,
        "test_db_template": settings.test_db_template,
        "test_isolation_mode": settings.test_isolation_mode,
        "test_isolation_auto_fallback": settings.test_isolation_auto_fallback,
        "llm_provider": settings.llm_provider,
        "llm_enabled": llm is not None,
    }


@app.post("/api/test-query", response_model=TestQueryResponse)
def test_query(request: TestQueryRequest) -> TestQueryResponse:
    try:
        response = graph.run(request)
        if response.status == "awaiting_approval":
            request_id = _store_pending_action(
                {
                    "kind": "test_query",
                    "request": request.model_dump(),
                }
            )
            response.approval_request_id = request_id
        return response
    except Exception as exc:
        logger.exception("Failed to process SQL test request")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        run_result = chat_workflow.run(request)
        response = run_result.response
        if response.status == "approval_needed" and run_result.pending_payload:
            request_id = _store_pending_action(
                {
                    "kind": "chat_sql_bundle",
                    "payload": run_result.pending_payload,
                }
            )
            response.approval_request_id = request_id
        return response
    except Exception as exc:
        logger.exception("Failed to process chat request")
        return ChatResponse(
            status="error",
            message=f"Chat workflow failed: {exc}",
        )


@app.post("/api/approve", response_model=ApprovalResponse)
def approve(request: ApprovalRequest) -> ApprovalResponse:
    action = _pop_pending_action(request.request_id)
    if not action:
        return ApprovalResponse(status="not_found", message="Approval request not found or expired.")

    if not request.approved:
        return ApprovalResponse(status="cancelled", message="Operation cancelled by user.")

    try:
        kind = action["kind"]
        if kind == "chat_sql_bundle":
            payload = action["payload"]
            chat_response = chat_workflow.execute_sql_bundle(
                sql_statements=payload["sql_statements"],
                preview_limit=int(payload.get("preview_limit", 200)),
            )
            return ApprovalResponse(
                status="success",
                message="Approved operation executed.",
                chat_response=chat_response,
            )

        if kind == "test_query":
            req_data = action["request"]
            req_data["approval_token"] = _default_approval_token()
            resumed = graph.run(TestQueryRequest(**req_data))
            return ApprovalResponse(
                status="success",
                message="Approved test execution resumed.",
                test_response=resumed,
            )

        return ApprovalResponse(status="error", message=f"Unsupported pending action kind: {kind}")
    except Exception as exc:
        logger.exception("Failed to resume approved request")
        return ApprovalResponse(status="error", message=str(exc))


def _store_pending_action(action: Dict[str, Any]) -> str:
    request_id = str(uuid.uuid4())
    with pending_lock:
        pending_actions[request_id] = action
    return request_id


def _pop_pending_action(request_id: str) -> Dict[str, Any] | None:
    with pending_lock:
        return pending_actions.pop(request_id, None)


def _default_approval_token() -> str:
    tokens = sorted(settings.normalized_approval_tokens)
    return tokens[0] if tokens else "yes"
