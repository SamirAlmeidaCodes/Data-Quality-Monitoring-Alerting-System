"""Unit tests for the null-rate rule."""

import pandas as pd
import pytest

from dqmonitor.errors import ConfigError
from dqmonitor.rules.null_rate import NullRateRule
from tests.conftest import context, make_rule


def _rule(**params) -> NullRateRule:
    params.setdefault("max_null_rate", 0.05)
    return make_rule(NullRateRule, **params)


def test_all_present_passes():
    frame = pd.DataFrame({"order_id": [1, 2, 3], "region": ["north", "south", "east"]})
    result = _rule().evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["column_rates"]["region"] == 0


def test_rate_equal_to_the_limit_passes():
    frame = pd.DataFrame({"region": ["north", None]})
    result = _rule(max_null_rate=0.5).evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["column_rates"]["region"] == 0.5


def test_rate_above_the_limit_fails():
    frame = pd.DataFrame({"region": ["north", None, None, "east"]})
    result = _rule(max_null_rate=0.25).evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["failing_columns"]["region"] == 0.5
    assert "region" in result.summary


def test_blank_and_whitespace_count_as_null():
    frame = pd.DataFrame({"region": ["north", "", "  ", "east"]})
    result = _rule(max_null_rate=0.0).evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["null_counts"]["region"] == 2


def test_blanks_can_be_ignored():
    frame = pd.DataFrame({"region": ["north", ""]})
    result = _rule(max_null_rate=0.0, treat_blank_as_null=False).evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["column_rates"]["region"] == 0


def test_only_listed_columns_are_checked():
    frame = pd.DataFrame({"order_id": [1, 2], "notes": [None, None]})
    result = _rule(max_null_rate=0.0, columns=["order_id"]).evaluate(frame, context())
    assert result.status == "pass"
    assert "notes" not in result.details["column_rates"]


def test_missing_column_fails():
    frame = pd.DataFrame({"order_id": [1]})
    result = _rule(columns=["order_id", "amount"]).evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["missing_columns"] == ["amount"]


def test_empty_dataset_fails():
    frame = pd.DataFrame({"order_id": pd.Series(dtype="object")})
    result = _rule().evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["row_count"] == 0


def test_unknown_parameter_is_rejected():
    with pytest.raises(ConfigError, match="unknown parameters"):
        _rule(threshold=0.1)
