"""Shared fixtures for rule, engine, and API tests."""

from pathlib import Path

import pandas as pd
import pytest
import yaml

from dqmonitor.rules.base import EvaluationContext
from dqmonitor.storage import sqlite_file_url

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in (
        "DQ_DATABASE_URL",
        "DQ_CONFIG",
        "DQ_SMTP_PASSWORD",
        "DQ_WEBHOOK_TOKEN",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


def context(**kwargs) -> EvaluationContext:
    kwargs.setdefault("source_name", "orders")
    return EvaluationContext(**kwargs)


def make_rule(cls, **params):
    name = params.pop("name", "check")
    source = params.pop("source", "orders")
    enabled = params.pop("enabled", True)
    return cls(name=name, source=source, enabled=enabled, params=params)


def write_frame(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def build_project(tmp_path: Path, *, rows: list[dict], rules: list[dict], run_on_startup: bool = True):
    """Write a one-source CSV project and return ``(config_path, folder, db_path)``."""

    folder = tmp_path / "incoming"
    write_frame(folder / "orders.csv", rows)
    database = tmp_path / "monitor.db"
    document = {
        "schedule": {"interval_minutes": 60, "run_on_startup": run_on_startup},
        "storage": {"url": sqlite_file_url(database)},
        "logging": {"level": "INFO"},
        "alerting": {"console": True, "on_statuses": ["fail", "error"]},
        "sources": [
            {
                "name": "orders",
                "type": "csv_folder",
                "path": str(folder),
                "pattern": "*.csv",
                "mode": "concat",
            }
        ],
        "rules": rules,
    }
    config_path = tmp_path / "rules.yaml"
    config_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return config_path, folder, database
