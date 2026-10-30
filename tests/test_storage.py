"""Monitoring table reads and writes."""

from datetime import datetime

from dqmonitor.storage import Store, sqlite_file_url


def _save(store: Store, **overrides):
    values = {
        "checked_at": datetime(2026, 10, 30, 12, 0, 0),
        "rule_name": "orders_null_rate",
        "rule_type": "null_rate",
        "source_name": "orders",
        "status": "pass",
        "summary": "ok",
        "details": {"column_rates": {"region": 0.0}},
    }
    values.update(overrides)
    return store.save(**values)


def test_latest_history_and_filters(tmp_path):
    store = Store(sqlite_file_url(tmp_path / "monitor.db"))
    store.create_tables()
    try:
        _save(store, checked_at=datetime(2026, 10, 30, 12, 0, 0), status="pass")
        _save(
            store,
            checked_at=datetime(2026, 10, 30, 13, 0, 0),
            status="fail",
            summary="too many nulls",
            details={"column_rates": {"region": 0.4}},
        )
        _save(
            store,
            checked_at=datetime(2026, 10, 30, 13, 0, 0),
            rule_name="orders_schema",
            rule_type="schema_consistency",
            status="pass",
            summary="schema ok",
        )
        latest = store.latest_by_rule("orders_null_rate")
        assert latest is not None
        assert latest.status == "fail"
        assert latest.details["column_rates"]["region"] == 0.4

        fails = store.history(status="fail")
        assert [item.rule_name for item in fails] == ["orders_null_rate"]

        window = store.history(since=datetime(2026, 10, 30, 12, 30, 0))
        assert len(window) == 2
        assert store.history(source_name="orders", limit=1)[0].rule_name == "orders_schema"
        assert store.latest_by_rule("missing") is None
    finally:
        store.dispose()
