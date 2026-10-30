"""Unit tests for the schema consistency rule."""

import pandas as pd
import pytest

from dqmonitor.errors import ConfigError
from dqmonitor.rules.schema import SchemaConsistencyRule
from tests.conftest import context, make_rule


def _rule(**params) -> SchemaConsistencyRule:
    params.setdefault("expected_columns", ["order_id", "amount"])
    return make_rule(SchemaConsistencyRule, **params)


def test_matching_schema_passes():
    frame = pd.DataFrame({"order_id": [1], "amount": [5]})
    result = _rule().evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["missing_columns"] == []


def test_extra_columns_pass_by_default():
    frame = pd.DataFrame({"order_id": [1], "amount": [5], "notes": ["x"]})
    result = _rule().evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["extra_columns"] == ["notes"]
    assert "notes" in result.summary


def test_extra_columns_fail_when_forbidden():
    frame = pd.DataFrame({"order_id": [1], "amount": [5], "notes": ["x"]})
    result = _rule(allow_extra=False).evaluate(frame, context())
    assert result.status == "fail"
    assert "Unexpected columns" in result.summary


def test_missing_column_fails():
    frame = pd.DataFrame({"order_id": [1]})
    result = _rule().evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["missing_columns"] == ["amount"]


def test_case_insensitive_match_passes():
    frame = pd.DataFrame({"Order_ID": [1], "Amount": [5]})
    result = _rule(case_sensitive=False).evaluate(frame, context())
    assert result.status == "pass"


def test_case_sensitive_mismatch_fails():
    frame = pd.DataFrame({"Order_ID": [1], "Amount": [5]})
    result = _rule(case_sensitive=True).evaluate(frame, context())
    assert result.status == "fail"


def test_one_input_file_missing_a_column_fails():
    frame = pd.DataFrame({"order_id": [1, 2], "amount": [5, None]})
    files = [
        {"name": "orders.csv", "columns": ["order_id", "amount"]},
        {"name": "orders_late.csv", "columns": ["order_id"]},
    ]
    result = _rule().evaluate(frame, context(file_schemas=files))
    assert result.status == "fail"
    assert result.details["files_missing_columns"]["orders_late.csv"] == ["amount"]
    assert "orders_late.csv" in result.summary


def test_empty_expected_columns_are_rejected():
    with pytest.raises(ConfigError, match="expected_columns"):
        _rule(expected_columns=[])
