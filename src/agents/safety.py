from __future__ import annotations

from dataclasses import dataclass
from typing import List

from src.models import QueryAnalysis


@dataclass
class SafetyDecision:
    requires_approval: bool
    reasons: List[str]


class SafetyAgent:
    """Human-in-the-loop guardrails for risky SQL execution."""

    def assess(self, analysis: QueryAnalysis) -> SafetyDecision:
        reasons: List[str] = []

        query_type = analysis.query_type.upper()
        destructive_types = {"DELETE", "DROP", "TRUNCATE", "UPDATE", "ALTER", "CREATE", "INSERT"}

        if analysis.is_destructive or query_type in destructive_types:
            reasons.append(f"Query type '{query_type}' can modify schema or data.")

        if query_type == "UPDATE" and not analysis.has_where:
            reasons.append("UPDATE without WHERE can affect all rows.")

        if query_type == "DELETE" and not analysis.has_where:
            reasons.append("DELETE without WHERE can remove all rows.")

        return SafetyDecision(requires_approval=bool(reasons), reasons=reasons)

    @staticmethod
    def is_approved(user_token: str | None, allowed_tokens: set[str]) -> bool:
        if not user_token:
            return False
        return user_token.strip().lower() in allowed_tokens
