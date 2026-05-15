from src.agents.safety import SafetyAgent
from src.models import QueryAnalysis


def test_safety_requires_approval_for_delete_without_where():
    agent = SafetyAgent()
    analysis = QueryAnalysis(query_type="DELETE", tables=["orders"], is_destructive=True, has_where=False)

    decision = agent.assess(analysis)

    assert decision.requires_approval is True
    assert any("without WHERE" in reason for reason in decision.reasons)


def test_safety_approval_token_check():
    assert SafetyAgent.is_approved("YES", {"yes", "y"}) is True
    assert SafetyAgent.is_approved("no", {"yes", "y"}) is False
