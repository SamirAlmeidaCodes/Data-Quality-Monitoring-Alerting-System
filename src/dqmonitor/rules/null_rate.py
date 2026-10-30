"""Fail when null or blank values exceed a configured rate."""

import pandas as pd

from dqmonitor.rules.base import (
    BaseRule,
    EvaluationContext,
    RuleResult,
    format_percent,
    round_rate,
)


def missing_mask(series: pd.Series, treat_blank_as_null: bool) -> pd.Series:
    """True where a value is null, and optionally where it is blank text."""

    mask = series.isna()
    if not treat_blank_as_null:
        return mask
    textual = (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_string_dtype(series)
        or isinstance(series.dtype, pd.CategoricalDtype)
    )
    if not textual:
        return mask
    blank = series.astype("string").str.strip().eq("").fillna(False)
    return mask | blank.astype(bool)


class NullRateRule(BaseRule):
    """Check the share of missing values in one or more columns.

    A column fails when its null rate is greater than ``max_null_rate``.
    The limit itself still passes. Omit ``columns`` to check every column.
    Blank strings count as null unless ``treat_blank_as_null`` is false.
    """

    type_name = "null_rate"
    allowed_params = frozenset({"columns", "max_null_rate", "treat_blank_as_null"})

    def validate(self) -> None:
        self.max_null_rate = self.require_float("max_null_rate", minimum=0, maximum=1)
        self.columns = self.optional_str_list("columns")
        self.treat_blank_as_null = self.optional_bool("treat_blank_as_null", True)

    def evaluate(self, df: pd.DataFrame, context: EvaluationContext) -> RuleResult:
        del context
        if df.empty:
            return RuleResult(
                status="fail",
                summary="Dataset is empty, so null rates cannot be measured.",
                details={"row_count": 0, "column_rates": {}, "missing_columns": []},
            )

        columns = self.columns or [str(column) for column in df.columns]
        missing_columns = [column for column in columns if column not in df.columns]
        column_rates: dict[str, float] = {}
        null_counts: dict[str, int] = {}
        for column in columns:
            if column not in df.columns:
                continue
            mask = missing_mask(df[column], self.treat_blank_as_null)
            null_counts[column] = int(mask.sum())
            column_rates[column] = round_rate(null_counts[column] / len(df))

        failing = {
            column: rate
            for column, rate in column_rates.items()
            if rate > self.max_null_rate
        }
        details = {
            "row_count": int(len(df)),
            "max_null_rate": self.max_null_rate,
            "treat_blank_as_null": self.treat_blank_as_null,
            "column_rates": column_rates,
            "null_counts": null_counts,
            "missing_columns": missing_columns,
            "failing_columns": failing,
        }
        limit = format_percent(self.max_null_rate)
        if missing_columns or failing:
            parts: list[str] = []
            if missing_columns:
                parts.append("missing columns: " + ", ".join(missing_columns))
            if failing:
                described = ", ".join(
                    f"{column}={format_percent(rate)}" for column, rate in failing.items()
                )
                parts.append(f"null rate above {limit}: {described}")
            return RuleResult(status="fail", summary="; ".join(parts), details=details)

        return RuleResult(
            status="pass",
            summary=f"All checked columns are at or below {limit} nulls.",
            details=details,
        )
