from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Dict, List

import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st

# Ensure `src` package imports work when Streamlit runs this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings

st.set_page_config(page_title="SamvadSQL", layout="wide")

API_BASE = settings.ui_api_base_url.rstrip("/")
CHAT_API = f"{API_BASE}/api/chat"
TEST_API = f"{API_BASE}/api/test-query"
APPROVE_API = f"{API_BASE}/api/approve"


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        .samvad-card {
            border: 1px solid #E6EAF2;
            border-radius: 14px;
            padding: 0.9rem 1rem;
            margin: 0.35rem 0 0.8rem 0;
            background: #FFFFFF;
            box-shadow: 0 1px 8px rgba(15, 23, 42, 0.04);
        }
        .samvad-card.fail {
            border-left: 5px solid #DC2626;
            background: #FEF2F2;
        }
        .samvad-card.pass {
            border-left: 5px solid #16A34A;
            background: #F0FDF4;
        }
        .samvad-label {
            font-size: 0.78rem;
            color: #475569;
            margin-bottom: 0.1rem;
        }
        .samvad-value {
            font-size: 1.03rem;
            font-weight: 600;
            color: #0F172A;
        }
        .samvad-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.6rem;
        }
        @media (max-width: 900px) {
            .samvad-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_state() -> None:
    defaults = {
        "chat_history": [],
        "chat_result": None,
        "chat_pending": None,
        "test_report": None,
        "test_pending": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _post_json(url: str, payload: Dict[str, Any], timeout: int = 180) -> Dict[str, Any]:
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _render_steps(steps: List[Dict[str, Any]]) -> None:
    if not steps:
        return
    with st.expander("Agent Steps", expanded=False):
        for step in steps:
            icon = step.get("icon", "•")
            message = step.get("message", "")
            st.write(f"{icon} {message}")


def _render_chart(rows: List[Dict[str, Any]], chart: Dict[str, Any]) -> None:
    if not rows or not chart:
        return
    df = pd.DataFrame(rows)
    chart_type = chart.get("chart_type", "table")
    x_col = chart.get("x")
    y_col = chart.get("y")
    title = chart.get("title") or "Result Chart"

    if chart_type == "table" or not x_col or not y_col:
        return
    if x_col not in df.columns or y_col not in df.columns:
        return

    fig, ax = plt.subplots(figsize=(8, 4))
    if chart_type == "bar":
        ax.bar(df[x_col].astype(str), df[y_col])
    elif chart_type == "line":
        ax.plot(df[x_col].astype(str), df[y_col], marker="o")
    elif chart_type == "pie":
        ax.pie(df[y_col], labels=df[x_col].astype(str), autopct="%1.1f%%")
    else:
        return

    ax.set_title(title)
    if chart_type != "pie":
        ax.set_xlabel(x_col)
        ax.set_ylabel(y_col)
    plt.xticks(rotation=30, ha="right")
    st.pyplot(fig)
    plt.close(fig)


def _render_analysis_cards(analysis: Dict[str, Any]) -> None:
    query_type = analysis.get("query_type") or "UNKNOWN"
    table_count = len(analysis.get("tables") or [])
    column_count = len(analysis.get("columns") or [])
    aggregation_count = len(analysis.get("aggregations") or [])
    parse_error = analysis.get("parse_error")

    st.markdown("#### Query Analysis")
    st.markdown(
        f"""
        <div class="samvad-grid">
            <div class="samvad-card"><div class="samvad-label">Query Type</div><div class="samvad-value">{query_type}</div></div>
            <div class="samvad-card"><div class="samvad-label">Tables</div><div class="samvad-value">{table_count}</div></div>
            <div class="samvad-card"><div class="samvad-label">Columns</div><div class="samvad-value">{column_count}</div></div>
            <div class="samvad-card"><div class="samvad-label">Aggregations</div><div class="samvad-value">{aggregation_count}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    d1, d2 = st.columns(2)
    d1.markdown("**Tables Referenced**")
    tables = analysis.get("tables") or []
    d1.write(tables if tables else ["None"])

    d2.markdown("**Columns Referenced**")
    cols = analysis.get("columns") or []
    d2.write(cols if cols else ["None"])

    if parse_error:
        st.error(f"Parse error: {parse_error}")

    with st.expander("Advanced Analysis Details", expanded=False):
        st.json(analysis)


def _render_scenario_results(report: Dict[str, Any]) -> None:
    results = report.get("results") or []
    scenarios = report.get("scenarios") or []
    if not results:
        st.info("No scenario results available.")
        return

    scenario_map = {item.get("scenario_id"): item for item in scenarios if item.get("scenario_id")}
    table_rows: List[Dict[str, Any]] = []
    for row in results:
        sid = row.get("scenario_id")
        status = row.get("status", "")
        icon = "✅" if status == "passed" else "❌"
        scenario_meta = scenario_map.get(sid, {})
        description = scenario_meta.get("description") or ""
        short_description = description[:90] + "..." if len(description) > 90 else description
        table_rows.append(
            {
                "Test Case ID": sid,
                "Title": row.get("name"),
                "Status": f"{icon} {status.upper()}",
                "Isolation": row.get("isolation_mode") or "n/a",
                "Execution (ms)": row.get("execution_time_ms"),
                "Rows": row.get("row_count"),
                "Description (short)": short_description,
            }
        )

    st.markdown("#### Scenario Overview")
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True)

    st.markdown("#### Scenario Details")
    for row in results:
        sid = row.get("scenario_id", "N/A")
        title = row.get("name", "Untitled scenario")
        status = row.get("status", "unknown")
        reason = row.get("reason")
        is_failed = status != "passed"
        card_class = "fail" if is_failed else "pass"
        status_icon = "❌" if is_failed else "✅"

        st.markdown(
            f"""
            <div class="samvad-card {card_class}">
                <div class="samvad-label">{sid}</div>
                <div class="samvad-value">{status_icon} {title}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        scenario_meta = scenario_map.get(sid, {})
        with st.expander(f"Description ({sid})", expanded=False):
            st.write(scenario_meta.get("description") or "No description available.")

        if reason:
            if is_failed:
                st.error(reason)
            else:
                st.info(reason)

        actual = row.get("sample_rows") or []
        expected = row.get("expected_rows") or []
        with st.expander(f"Actual vs Expected ({sid})", expanded=False):
            col_a, col_e = st.columns(2)
            col_a.markdown("**Actual**")
            col_a.code(json.dumps(actual, indent=2), language="json")
            col_e.markdown("**Expected**")
            col_e.code(json.dumps(expected, indent=2), language="json")


def _render_chat_tab() -> None:
    st.subheader("Chat With Database")
    auto_generate = st.checkbox("Auto-generate missing schema/data", value=True, key="chat_auto_generate")
    preview_limit = st.slider("Result preview rows", min_value=10, max_value=1000, value=200, key="chat_preview_limit")

    user_message = st.text_area(
        "Ask in plain English (or paste SQL)",
        key="chat_input",
        height=120,
        placeholder="Show me customers with paid orders above 100",
    )

    if st.button("Send", type="primary", key="chat_send"):
        if not user_message.strip():
            st.warning("Enter a request first.")
        else:
            st.session_state.chat_history.append({"role": "user", "content": user_message})
            try:
                body = _post_json(
                    CHAT_API,
                    {
                        "message": user_message,
                        "auto_generate_missing_data": auto_generate,
                        "preview_limit": preview_limit,
                    },
                )
            except requests.RequestException as exc:
                st.error(f"Chat request failed: {exc}")
            else:
                status = body.get("status")
                if status == "approval_needed":
                    st.session_state.chat_pending = body
                    st.session_state.chat_result = None
                elif status == "success":
                    st.session_state.chat_result = body
                    st.session_state.chat_pending = None
                    st.session_state.chat_history.append(
                        {"role": "assistant", "content": body.get("message", "Done.")}
                    )
                else:
                    st.session_state.chat_result = None
                    st.session_state.chat_pending = None
                    st.error(body.get("message", "Unexpected response from backend."))

    pending = st.session_state.chat_pending
    if pending:
        st.warning(pending.get("approval_reason") or "Approval required.")
        st.code("\n".join(pending.get("sql", [])), language="sql")
        left, right = st.columns(2)
        request_id = pending.get("approval_request_id")
        if left.button("Yes, execute", key="chat_approve_yes"):
            if request_id:
                approval = _post_json(APPROVE_API, {"request_id": request_id, "approved": True})
                chat_response = approval.get("chat_response")
                if chat_response:
                    st.session_state.chat_result = chat_response
                    st.session_state.chat_history.append(
                        {"role": "assistant", "content": chat_response.get("message", "Approved and executed.")}
                    )
                st.session_state.chat_pending = None
                st.success(approval.get("message", "Approved operation executed."))
        if right.button("No, cancel", key="chat_approve_no"):
            if request_id:
                _post_json(APPROVE_API, {"request_id": request_id, "approved": False})
            st.session_state.chat_pending = None
            st.info("Operation cancelled.")

    result = st.session_state.chat_result
    if result:
        _render_steps(result.get("steps", []))
        message = result.get("message", "Completed.")
        if "authentication failed" in message.lower():
            st.error(message)
            st.info(
                "Tip: verify `.env` credentials and test manually using `psql -h <host> -U <user> -d <db>`."
            )
        else:
            st.success(message)
        rows = result.get("rows", [])
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
            _render_chart(rows, result.get("chart") or {})
        if result.get("sql"):
            with st.expander("Executed SQL", expanded=False):
                st.code("\n".join(result["sql"]), language="sql")

    if st.session_state.chat_history:
        with st.expander("Chat History", expanded=False):
            for item in st.session_state.chat_history[-20:]:
                role = "You" if item["role"] == "user" else "Assistant"
                st.write(f"**{role}:** {item['content']}")


def _report_to_markdown(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# SQL Test Report",
        f"- Query: `{report.get('sql_query', '')}`",
        f"- Total: {summary.get('total', 0)}",
        f"- Passed: {summary.get('passed', 0)}",
        f"- Failed: {summary.get('failed', 0)}",
        "",
        "## Results",
    ]
    for item in report.get("results", []):
        status = "✅" if item.get("status") == "passed" else "❌"
        lines.append(f"- {status} {item.get('name')} ({item.get('scenario_id')})")
        if item.get("reason"):
            lines.append(f"  - Reason: {item['reason']}")
    return "\n".join(lines)


def _render_sql_test_tab() -> None:
    st.subheader("SQL Query Testing")
    sql_query = st.text_area(
        "SQL Query",
        height=180,
        key="sql_test_query",
        placeholder="SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id",
    )
    c1, c2, c3, c4 = st.columns(4)
    positive_cases = c1.number_input("Positive", min_value=0, max_value=20, value=3)
    negative_cases = c2.number_input("Negative", min_value=0, max_value=20, value=2)
    edge_cases = c3.number_input("Edge", min_value=0, max_value=20, value=1)
    max_scenarios = c4.number_input("Max scenarios", min_value=1, max_value=20, value=8)
    include_perf = st.checkbox("Include performance scenario", value=False, key="sql_include_perf")
    generate_missing = st.checkbox("Generate missing data", value=True, key="sql_generate_missing")

    if st.button("Test Query", type="primary", key="sql_test_run"):
        if not sql_query.strip():
            st.warning("Enter SQL query first.")
        else:
            with st.status("Running agent workflow...", expanded=True) as status:
                st.write("🔍 Parsing query and schema")
                try:
                    body = _post_json(
                        TEST_API,
                        {
                            "sql_query": sql_query,
                            "generate_missing_data": generate_missing,
                            "include_performance_scenario": include_perf,
                            "positive_cases": int(positive_cases),
                            "negative_cases": int(negative_cases),
                            "edge_cases": int(edge_cases),
                            "max_scenarios": int(max_scenarios),
                        },
                    )
                except requests.RequestException as exc:
                    status.update(label="Failed", state="error")
                    st.error(f"Test request failed: {exc}")
                else:
                    if body.get("status") == "awaiting_approval":
                        st.session_state.test_pending = body
                        st.session_state.test_report = None
                        status.update(label="⚠️ Approval needed", state="warning")
                    elif body.get("status") == "success":
                        st.session_state.test_report = body.get("report")
                        st.session_state.test_pending = None
                        status.update(label="✅ Complete", state="complete")
                    else:
                        status.update(label="Unexpected response", state="error")
                        st.error(str(body))

    pending = st.session_state.test_pending
    if pending:
        st.warning(pending.get("approval_reason") or "Approval required for risky query execution.")
        preview = pending.get("scenario_preview", [])
        if preview:
            preview_rows = []
            for item in preview:
                preview_rows.append(
                    {
                        "Test Case ID": item.get("scenario_id"),
                        "Title": item.get("name"),
                        "Description": item.get("description"),
                    }
                )
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)
        left, right = st.columns(2)
        request_id = pending.get("approval_request_id")
        if left.button("Approve and run", key="sql_approve_yes"):
            if request_id:
                approval = _post_json(APPROVE_API, {"request_id": request_id, "approved": True})
                resumed = approval.get("test_response")
                if resumed and resumed.get("status") == "success":
                    st.session_state.test_report = resumed.get("report")
                st.session_state.test_pending = None
                st.success(approval.get("message", "Approved."))
        if right.button("Cancel", key="sql_approve_no"):
            if request_id:
                _post_json(APPROVE_API, {"request_id": request_id, "approved": False})
            st.session_state.test_pending = None
            st.info("Execution cancelled.")

    report = st.session_state.test_report
    if report:
        summary = report.get("summary", {})
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scenarios", summary.get("total", 0))
        m2.metric("Passed", summary.get("passed", 0))
        m3.metric("Failed", summary.get("failed", 0))
        m4.metric("Pass %", summary.get("pass_rate", 0))

        _render_analysis_cards(report.get("analysis", {}))
        _render_scenario_results(report)

        notes = report.get("notes", [])
        if notes:
            st.subheader("Insights")
            for note in notes:
                st.write(f"- {note}")

        report_md = _report_to_markdown(report)
        st.download_button(
            "Download Report (.md)",
            data=report_md.encode("utf-8"),
            file_name="sql_test_report.md",
            mime="text/markdown",
        )


def main() -> None:
    _inject_styles()
    _init_state()
    st.title("SamvadSQL Agentic SQL Assistant")
    st.caption("Chat with your database or run robust SQL tests with approval-safe execution.")
    tab_chat, tab_test = st.tabs(["Chat", "SQL Test"])
    with tab_chat:
        _render_chat_tab()
    with tab_test:
        _render_sql_test_tab()


if __name__ == "__main__":
    main()
