from __future__ import annotations

from typing import Any, Dict


class DataAuditor:
    def __init__(self, llm: Any = None):
        self.llm = llm

    def scenario_needs_data(self, scenario: Dict, existing_db_conn: Any = None) -> bool:
        """Hackathon behavior: always generate fresh data per scenario."""
        return True

    # Keep legacy method name for compatibility
    def scenario_needs_data_legacy(self, scenario: Dict, existing_db_conn: Any = None) -> bool:
        return self.scenario_needs_data(scenario, existing_db_conn)
