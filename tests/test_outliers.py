"""Unit tests for the statistical outlier rule."""

import pandas as pd
import pytest

from dqmonitor.errors import ConfigError
from dqmonitor.rules.outliers import StatisticalOutliersRule
from tests.conftest import context, make_rule


def _rule(**params) -> StatisticalOutliersRule:
    params.setdefault("column", "amount")
    params.setdefault("max_outlier_rate", 0.05)
    return make_rule(StatisticalOutliersRule, **params)


def test_iqr_with_no_outliers_passes():
    frame = pd.DataFrame({"amount": [10, 11, 12, 13, 14, 15, 16, 17]})
    result = _rule().evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["outlier_count"] == 0
    assert result.details["method"] == "iqr"


def test_iqr_outlier_above_the_limit_fails():
    frame = pd.DataFrame({"amount": [1, 1, 1, 1, 1, 1, 1, 100]})
    result = _rule(max_outlier_rate=0.05).evaluate(frame, context())
    assert result.status == "fail"
    assert result.details["outlier_count"] == 1
    assert result.details["outlier_rate"] == 0.125
    assert 100 in result.details["outlier_examples"]


def test_iqr_rate_equal_to_the_limit_passes():
    frame = pd.DataFrame({"amount": [1, 1, 1, 1, 1, 1, 1, 100]})
    result = _rule(max_outlier_rate=0.125).evaluate(frame, context())
    assert result.status == "pass"


def test_constant_series_has_no_outliers():
    frame = pd.DataFrame({"amount": [5, 5, 5, 5]})
    iqr = _rule(method="iqr").evaluate(frame, context())
    zscore = _rule(method="zscore").evaluate(frame, context())
    assert iqr.status == "pass"
    assert zscore.status == "pass"
    assert zscore.details["outlier_count"] == 0


def test_zscore_flags_an_extreme_value():
    frame = pd.DataFrame({"amount": [10] * 20 + [10000]})
    result = _rule(method="zscore", zscore_threshold=3, max_outlier_rate=0.01).evaluate(
        frame, context()
    )
    assert result.status == "fail"
    assert result.details["outlier_count"] == 1


def test_zscore_on_a_tight_series_passes():
    frame = pd.DataFrame({"amount": [10, 11, 12, 13, 10, 11, 12, 13]})
    result = _rule(method="zscore", zscore_threshold=3, max_outlier_rate=0.0).evaluate(
        frame, context()
    )
    assert result.status == "pass"
    assert result.details["outlier_count"] == 0


def test_non_numeric_column_is_an_error():
    frame = pd.DataFrame({"amount": ["a", "b", "c"]})
    result = _rule().evaluate(frame, context())
    assert result.status == "error"
    assert result.details["numeric_count"] == 0


def test_mixed_values_ignore_text():
    frame = pd.DataFrame({"amount": [1, 2, "x", 3]})
    result = _rule(max_outlier_rate=1).evaluate(frame, context())
    assert result.status == "pass"
    assert result.details["non_numeric_count"] == 1
    assert result.details["numeric_count"] == 3


def test_missing_column_fails():
    frame = pd.DataFrame({"other": [1, 2, 3]})
    result = _rule().evaluate(frame, context())
    assert result.status == "fail"
    assert "amount" in result.summary


def test_zscore_needs_two_values():
    frame = pd.DataFrame({"amount": [10]})
    result = _rule(method="zscore").evaluate(frame, context())
    assert result.status == "error"


def test_unknown_method_is_rejected():
    with pytest.raises(ConfigError, match="method"):
        _rule(method="mad")
