from __future__ import annotations

from typing import List

from src.models import SQLTestReport, ScenarioExecutionResult


class ReportBuilder:
    def summarize(self, report: SQLTestReport) -> SQLTestReport:
        report.summary = self._build_summary(report.results)
        report.notes.extend(self._build_notes(report))
        return report

    @staticmethod
    def _build_summary(results: List[ScenarioExecutionResult]) -> dict:
        total = len(results)
        passed = sum(1 for result in results if result.status == "passed")
        failed = sum(1 for result in results if result.status == "failed")
        skipped = sum(1 for result in results if result.status == "skipped")

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": round((passed / total) * 100, 2) if total else 0.0,
        }

    @staticmethod
    def _build_notes(report: SQLTestReport) -> list[str]:
        notes: list[str] = []

        if report.analysis.parse_error:
            notes.append("Query parsing failed; fix SQL syntax before running scenarios.")

        if not report.analysis.tables:
            notes.append("No base tables were detected. Query might be constant-select or malformed.")

        if report.summary.get("failed", 0) > 0:
            notes.append("Inspect failed scenarios for root causes and apply suggested query fixes.")

        if report.summary.get("total", 0) == 0:
            notes.append("No scenarios were executed.")

        isolation_modes = {
            result.isolation_mode
            for result in report.results
            if getattr(result, "isolation_mode", None)
        }
        if "transaction" in isolation_modes:
            notes.append("Scenario isolation used transaction sandbox with rollback by default.")
        if "database" in isolation_modes:
            notes.append(
                "Some scenarios used isolated temporary databases due to non-transaction-safe SQL."
            )

        return notes
