"""YAML loading and rule compilation."""

import pytest

from dqmonitor.config import load_application
from dqmonitor.errors import ConfigError
from tests.conftest import ROOT


def test_shipped_config_builds_nine_rules():
    compiled = load_application(ROOT / "config" / "rules.yaml")
    assert compiled.settings.schedule.interval_minutes == 60
    assert compiled.settings.schedule.label() == "Every 60 minutes"
    names = [rule.name for rule in compiled.rules]
    assert names == [
        "orders_null_rate",
        "orders_row_count",
        "orders_duplicate_spike",
        "orders_amount_outliers",
        "orders_schema",
        "customers_null_rate",
        "customers_row_count",
        "customers_duplicate_spike",
        "customers_schema",
    ]
    null_rule = compiled.rules[0]
    assert null_rule.max_null_rate == 0.05
    assert "region" in null_rule.columns


def test_interval_seconds_override_the_label(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(
        "schedule:\n  interval_minutes: 60\n  interval_seconds: 30\nsources: []\nrules: []\n",
        encoding="utf-8",
    )
    compiled = load_application(path)
    assert compiled.settings.schedule.label() == "Every 30 seconds"
    assert compiled.settings.schedule.trigger_kwargs() == {"seconds": 30}


def test_unknown_rule_type(tmp_path):
    path = _write(
        tmp_path,
        """
sources:
  - name: orders
    type: csv_folder
    path: data
rules:
  - name: bad
    type: freshness
    source: orders
""",
    )
    with pytest.raises(ConfigError, match="Unknown rule type"):
        load_application(path)


def test_unknown_parameter(tmp_path):
    path = _write(
        tmp_path,
        """
sources:
  - name: orders
    type: csv_folder
    path: data
rules:
  - name: orders_nulls
    type: null_rate
    source: orders
    max_null_rate: 0.1
    threhold: 0.1
""",
    )
    with pytest.raises(ConfigError, match="unknown parameters"):
        load_application(path)


def test_rule_source_must_exist(tmp_path):
    path = _write(
        tmp_path,
        """
sources:
  - name: orders
    type: csv_folder
    path: data
rules:
  - name: orders_schema
    type: schema_consistency
    source: missing
    expected_columns: [order_id]
""",
    )
    with pytest.raises(ConfigError, match="unknown source"):
        load_application(path)


def test_duplicate_rule_names(tmp_path):
    path = _write(
        tmp_path,
        """
sources:
  - name: orders
    type: csv_folder
    path: data
rules:
  - name: same
    type: schema_consistency
    source: orders
    expected_columns: [order_id]
  - name: same
    type: schema_consistency
    source: orders
    expected_columns: [order_id]
""",
    )
    with pytest.raises(ConfigError, match="unique"):
        load_application(path)


def test_top_level_typo_is_rejected(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("rulez: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="rulez"):
        load_application(path)


def test_sqlite_table_must_be_an_identifier(tmp_path):
    path = _write(
        tmp_path,
        """
sources:
  - name: warehouse
    type: sqlite
    url: sqlite:///data/warehouse.db
    table: "orders;drop"
rules: []
""",
    )
    with pytest.raises(ConfigError, match="identifier"):
        load_application(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_application(tmp_path / "missing.yaml")


def test_database_url_env_override(tmp_path, monkeypatch):
    path = tmp_path / "rules.yaml"
    path.write_text("storage:\n  url: sqlite:///data/monitor.db\n", encoding="utf-8")
    monkeypatch.setenv("DQ_DATABASE_URL", "sqlite:///tmp/override.db")
    compiled = load_application(path)
    assert compiled.settings.storage.url == "sqlite:///tmp/override.db"


def _write(tmp_path, text: str):
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path
