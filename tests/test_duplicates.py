"""Unit tests for the duplicate-spike rule."""

import pandas as pd
import pytest

from dqmonitor.errors import ConfigError
from dqmonitor.rules.duplicates import DuplicateSpikeRule
from tests.conftest import context, make_rule


def _rule(**params) -> DuplicateSpikeRule:
    params.setdefault("key_columns", ["order_id"])
    params.setdefault("max_duplicate_rate", 0.0)
    return make_rule(DuplicateSpikeRule, **params)


def test_unique_keys_pass():
    frame = pd.DataFrame({"order_id": [1, 2, 3]})
    result = _rule().evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["duplicate_rate"] == 0


def test_rate_above_the_limit_fails():
    frame = pd.DataFrame({"order_id": [1, 1, 2, 3]})
    result = _rule(max_duplicate_rate=0.2).evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["duplicate_rows"] == 1
    assert result.details["duplicate_rate"] == 0.25


def test_rate_equal_to_the_limit_passes():
    frame = pd.DataFrame({"order_id": [1, 1, 2, 3]})
    result = _rule(max_duplicate_rate=0.25).evaluate(frame, context())
    assert result.status == "pass"


def test_composite_key():
    frame = pd.DataFrame(
        {"order_id": [1, 1, 1], "line": [1, 2, 1], "amount": [5, 6, 7]}
    )
    result = _rule(key_columns=["order_id", "line"], max_duplicate_rate=0.0).evaluate(
        frame, context()
    )
    assert result.status == "fail"
    assert result.details["unique_keys"] == 2
    assert result.details["duplicate_rows"] == 1


def test_null_keys_count_as_duplicates():
    frame = pd.DataFrame({"order_id": [1, None, None]})
    result = _rule().evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["duplicate_rows"] == 1


def test_missing_key_column_fails():
    frame = pd.DataFrame({"amount": [1, 2]})
    result = _rule().evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["missing_columns"] == ["order_id"]


def test_empty_dataset_fails():
    frame = pd.DataFrame({"order_id": pd.Series(dtype="int64")})
    result = _rule().evaluate(frame, context())
    assert result.status == "fail"
    assert "empty" in result.summary


def test_duplicate_key_names_are_rejected():
    with pytest.raises(ConfigError, match="duplicates"):
        _rule(key_columns=["order_id", "order_id"])
