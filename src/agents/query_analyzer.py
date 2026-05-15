from __future__ import annotations

from typing import List, Optional

from langchain_core.language_models import BaseChatModel
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from src.models import QueryAnalysis


class QueryAnalyzer:
    def __init__(self, llm: Optional[BaseChatModel] = None):
        self.llm = llm

    def analyze(self, sql_query: str) -> QueryAnalysis:
        try:
            expression = parse_one(sql_query, read="postgres")
        except ParseError as exc:
            return QueryAnalysis(
                query_type="UNKNOWN",
                parse_error=str(exc),
                is_destructive=False,
            )

        tables = sorted({self._table_name(table) for table in expression.find_all(exp.Table)})
        columns = sorted({column.sql(dialect="postgres") for column in expression.find_all(exp.Column)})

        conditions: List[str] = []
        for node in expression.find_all(exp.Where):
            if node.this is not None:
                conditions.append(node.this.sql(dialect="postgres"))
        for node in expression.find_all(exp.Having):
            if node.this is not None:
                conditions.append(node.this.sql(dialect="postgres"))

        aggregations = sorted(
            {
                node.sql_name().upper()
                for node in expression.find_all(exp.AggFunc)
                if hasattr(node, "sql_name")
            }
        )

        group_by: List[str] = []
        group_node = expression.find(exp.Group)
        if group_node:
            group_by = [expr.sql(dialect="postgres") for expr in group_node.expressions]

        order_by: List[str] = []
        order_node = expression.find(exp.Order)
        if order_node:
            order_by = [expr.sql(dialect="postgres") for expr in order_node.expressions]

        limit_value = None
        limit_node = expression.find(exp.Limit)
        if limit_node and limit_node.expression:
            try:
                limit_value = int(limit_node.expression.this)
            except (TypeError, ValueError):
                limit_value = None

        query_type = expression.key.upper()
        is_destructive = self._is_destructive_expression(expression)

        return QueryAnalysis(
            query_type=query_type,
            tables=tables,
            columns=columns,
            conditions=conditions,
            aggregations=aggregations,
            group_by=group_by,
            order_by=order_by,
            limit=limit_value,
            is_destructive=is_destructive,
            has_where=expression.find(exp.Where) is not None,
        )

    @staticmethod
    def _table_name(table: exp.Table) -> str:
        if table.db:
            return f"{table.db}.{table.name}"
        return table.name

    @staticmethod
    def _is_destructive_expression(expression: exp.Expression) -> bool:
        destructive_types = (
            exp.Delete,
            exp.Drop,
            exp.TruncateTable,
            exp.Update,
            exp.Alter,
            exp.Create,
        )
        return isinstance(expression, destructive_types)
