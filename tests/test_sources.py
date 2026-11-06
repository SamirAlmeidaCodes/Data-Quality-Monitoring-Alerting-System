"""CSV folder and SQLite source loading."""

import os

import pandas as pd
import pytest
from sqlalchemy import create_engine

from pydantic import ValidationError

from dqmonitor.config_models import CsvFolderSourceConfig, SqliteSourceConfig
from dqmonitor.errors import SourceError
from dqmonitor.sources import load_source
from dqmonitor.storage import sqlite_file_url


def _csv_source(path, **kwargs) -> CsvFolderSourceConfig:
    values = {
        "name": "orders",
        "type": "csv_folder",
        "path": str(path),
        "pattern": "*.csv",
        "mode": "concat",
    }
    values.update(kwargs)
    return CsvFolderSourceConfig(**values)


def test_concat_reads_every_match_and_ignores_other_files(tmp_path):
    pd.DataFrame({"order_id": [1]}).to_csv(tmp_path / "orders_a.csv", index=False)
    pd.DataFrame({"order_id": [2], "notes": ["later"]}).to_csv(tmp_path / "orders_b.csv", index=False)
    pd.DataFrame({"customer_id": [9]}).to_csv(tmp_path / "customers.csv", index=False)
    frame = load_source(_csv_source(tmp_path, pattern="orders*.csv"))
    assert sorted(frame["order_id"].tolist()) == [1, 2]
    assert "customer_id" not in frame.columns
    assert [item["name"] for item in frame.attrs["dq_meta"]["files"]] == [
        "orders_a.csv",
        "orders_b.csv",
    ]


def test_latest_keeps_the_newest_file(tmp_path):
    old = tmp_path / "orders_old.csv"
    new = tmp_path / "orders_new.csv"
    pd.DataFrame({"order_id": [1]}).to_csv(old, index=False)
    pd.DataFrame({"order_id": [2]}).to_csv(new, index=False)
    os.utime(old, (1, 1))
    os.utime(new, (2_000, 2_000))
    frame = load_source(_csv_source(tmp_path, mode="latest"))
    assert frame["order_id"].tolist() == [2]
    assert frame.attrs["dq_meta"]["files"][0]["name"] == "orders_new.csv"


def test_single_file_path(tmp_path):
    path = tmp_path / "orders.csv"
    pd.DataFrame({"order_id": [4]}).to_csv(path, index=False)
    frame = load_source(_csv_source(path, pattern="ignored*.csv"))
    assert frame["order_id"].tolist() == [4]
    assert frame.attrs["dq_meta"]["mode"] == "file"


def test_csv_pattern_cannot_leave_the_directory(tmp_path):
    with pytest.raises(ValidationError, match="inside"):
        _csv_source(tmp_path, pattern="../*.csv")


def test_symlink_outside_the_directory_is_rejected(tmp_path):
    outside = tmp_path / "secret.csv"
    pd.DataFrame({"order_id": [1]}).to_csv(outside, index=False)
    folder = tmp_path / "incoming"
    folder.mkdir()
    (folder / "link.csv").symlink_to(outside)
    with pytest.raises(SourceError, match="outside"):
        load_source(_csv_source(folder))


def test_missing_folder_raises(tmp_path):
    with pytest.raises(SourceError, match="does not exist"):
        load_source(_csv_source(tmp_path / "missing"))


def test_sqlite_table_round_trip(tmp_path):
    database = tmp_path / "warehouse.db"
    engine = create_engine(sqlite_file_url(database))
    pd.DataFrame({"order_id": [1, 2], "amount": [5.0, 6.0]}).to_sql(
        "orders", engine, index=False
    )
    engine.dispose()
    frame = load_source(
        SqliteSourceConfig(
            name="warehouse",
            type="sqlite",
            url=sqlite_file_url(database),
            table="orders",
        )
    )
    assert frame["order_id"].tolist() == [1, 2]
    assert frame.attrs["dq_meta"]["files"][0]["name"] == "orders"


def test_missing_sqlite_file_does_not_create_a_database(tmp_path):
    database = tmp_path / "missing.db"
    with pytest.raises(SourceError, match="does not exist"):
        load_source(
            SqliteSourceConfig(
                name="warehouse",
                type="sqlite",
                url=sqlite_file_url(database),
                table="orders",
            )
        )
    assert not database.exists()
