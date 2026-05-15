from src.agents.query_analyzer import QueryAnalyzer


def test_query_analyzer_extracts_tables_and_columns():
    analyzer = QueryAnalyzer()
    analysis = analyzer.analyze(
        "SELECT customer_id, SUM(amount) FROM orders WHERE status = 'paid' GROUP BY customer_id"
    )

    assert analysis.parse_error is None
    assert analysis.query_type == "SELECT"
    assert "orders" in analysis.tables
    assert any("customer_id" in column for column in analysis.columns)
    assert "SUM" in analysis.aggregations
    assert analysis.has_where is True


def test_query_analyzer_flags_destructive_query():
    analyzer = QueryAnalyzer()
    analysis = analyzer.analyze("DELETE FROM orders")

    assert analysis.query_type == "DELETE"
    assert analysis.is_destructive is True
