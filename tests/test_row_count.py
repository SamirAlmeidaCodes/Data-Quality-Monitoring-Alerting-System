"""Unit tests for the row-count drop rule."""

import pandas as pd

from dqmonitor.rules.row_count import RowCountDropRule
from tests.conftest import context, make_rule


def _rule(**params) -> RowCountDropRule:
    params.setdefault("max_drop_ratio", 0.3)
    params.setdefault("min_rows", 1)
    return make_rule(RowCountDropRule, **params)


def _frame(count: int) -> pd.DataFrame:
    return pd.DataFrame({"order_id": range(count)})


def test_first_run_records_a_baseline():
    result = _rule().evaluate(_frame(10), context())
    assert result.status == "pass"
    assert result.details["row_count"] == 10
    assert result.details["previous_row_count"] is None
    assert "No earlier row count" in result.summary


def test_below_minimum_fails_on_the_first_run():
    result = _rule(min_rows=5).evaluate(_frame(2), context())
    assert result.status == "fail"
    assert result.details["row_count"] == 2
    assert "minimum" in result.summary


def test_drop_within_the_limit_passes():
    result = _rule().evaluate(_frame(80), context(previous_details={"row_count": 100}))
    assert result.status == "pass"
    assert result.details["drop_ratio"] == 0.2


def test_drop_above_the_limit_fails():
    result = _rule().evaluate(_frame(50), context(previous_details={"row_count": 100}))
    assert result.status == "fail"
    assert result.details["drop_ratio"] == 0.5
    assert "dropped" in result.summary


def test_drop_equal_to_the_limit_passes():
    result = _rule(max_drop_ratio=0.3).evaluate(
        _frame(70), context(previous_details={"row_count": 100})
    )
    assert result.status == "pass"
    assert result.details["drop_ratio"] == 0.3


def test_growth_passes():
    result = _rule().evaluate(_frame(120), context(previous_details={"row_count": 100}))
    assert result.status == "pass"
    assert "grew" in result.summary


def test_growth_from_zero_passes():
    result = _rule().evaluate(_frame(5), context(previous_details={"row_count": 0}))
    assert result.status == "pass"


def test_missing_previous_count_is_a_new_baseline():
    result = _rule().evaluate(_frame(4), context(previous_details={"duration_ms": 1}))
    assert result.status == "pass"
    assert result.details["previous_row_count"] is None


def test_minimum_and_drop_are_both_reported():
    result = _rule(min_rows=10, max_drop_ratio=0.2).evaluate(
        _frame(4), context(previous_details={"row_count": 20})
    )
    assert result.status == "fail"
    assert "minimum" in result.summary
    assert "dropped" in result.summary
