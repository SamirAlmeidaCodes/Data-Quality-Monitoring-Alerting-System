"""Fail when too many numeric values sit outside a statistical fence."""

from dataclasses import dataclass

import pandas as pd

from dqmonitor.errors import ConfigError
from dqmonitor.rules.base import (
    BaseRule,
    EvaluationContext,
    RuleResult,
    format_percent,
    round_rate,
)


@dataclass
class _Fence:
    lower: float
    upper: float
    extra: dict


def _iqr_fence(values: pd.Series, multiplier: float) -> tuple[pd.Series, _Fence]:
    q1 = float(values.quantile(0.25))
    q3 = float(values.quantile(0.75))
    iqr = q3 - q1
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr
    mask = (values < lower) | (values > upper)
    fence = _Fence(
        lower=lower,
        upper=upper,
        extra={"q1": round(q1, 6), "q3": round(q3, 6), "iqr": round(float(iqr), 6)},
    )
    return mask, fence


def _zscore_fence(values: pd.Series, threshold: float) -> tuple[pd.Series, _Fence]:
    mean = float(values.mean())
    std = float(values.std(ddof=1))
    if std == 0:
        mask = pd.Series(False, index=values.index)
        return mask, _Fence(lower=mean, upper=mean, extra={"mean": round(mean, 6), "std": 0.0})
    zscores = (values - mean).abs() / std
    mask = zscores > threshold
    fence = _Fence(
        lower=mean - threshold * std,
        upper=mean + threshold * std,
        extra={"mean": round(mean, 6), "std": round(std, 6)},
    )
    return mask, fence


class StatisticalOutliersRule(BaseRule):
    """Flag outliers with an IQR fence or a z-score.

    Z-scores use the sample standard deviation (ddof=1).

    Nulls and values that cannot be parsed as numbers are left out of the
    rate. The denominator is the number of numeric values. The check fails
    when ``outlier_count / numeric_count`` is greater than ``max_outlier_rate``.
    """

    type_name = "statistical_outliers"
    allowed_params = frozenset(
        {
            "column",
            "method",
            "max_outlier_rate",
            "iqr_multiplier",
            "zscore_threshold",
        }
    )

    def validate(self) -> None:
        self.column = self.require_str("column")
        self.method = self.optional_str("method", "iqr", choices={"iqr", "zscore"})
        self.max_outlier_rate = self.require_float("max_outlier_rate", minimum=0, maximum=1)
        self.iqr_multiplier = self.optional_float("iqr_multiplier", 1.5, minimum=0)
        self.zscore_threshold = self.optional_float("zscore_threshold", 3.0, minimum=0)
        if self.method == "zscore" and self.zscore_threshold <= 0:
            raise ConfigError(
                f"Rule '{self.name}' parameter 'zscore_threshold' must be greater than 0"
            )

    def evaluate(self, df: pd.DataFrame, context: EvaluationContext) -> RuleResult:
        del context
        if self.column not in df.columns:
            return RuleResult(
                status="fail",
                summary=f"Column '{self.column}' is not in the dataset.",
                details={"column": self.column, "method": self.method},
            )

        original = df[self.column]
        numeric = pd.to_numeric(original, errors="coerce")
        non_numeric_count = int((~original.isna() & numeric.isna()).sum())
        null_count = int(original.isna().sum())
        values = numeric.dropna()
        details: dict = {
            "column": self.column,
            "method": self.method,
            "row_count": int(len(df)),
            "null_count": null_count,
            "non_numeric_count": non_numeric_count,
            "numeric_count": int(len(values)),
            "max_outlier_rate": self.max_outlier_rate,
        }
        if values.empty:
            return RuleResult(
                status="error",
                summary=f"Column '{self.column}' has no numeric values to test.",
                details=details,
            )
        if self.method == "zscore" and len(values) < 2:
            return RuleResult(
                status="error",
                summary="Z-score needs at least 2 numeric values.",
                details=details,
            )

        if self.method == "iqr":
            mask, fence = _iqr_fence(values, self.iqr_multiplier)
            details["iqr_multiplier"] = self.iqr_multiplier
        else:
            mask, fence = _zscore_fence(values, self.zscore_threshold)
            details["zscore_threshold"] = self.zscore_threshold

        outlier_count = int(mask.sum())
        rate = round_rate(outlier_count / len(values))
        examples = [round(float(value), 6) for value in values[mask].head(5).tolist()]
        details.update(
            {
                "outlier_count": outlier_count,
                "outlier_rate": rate,
                "lower_bound": round(fence.lower, 6),
                "upper_bound": round(fence.upper, 6),
                "outlier_examples": examples,
                **fence.extra,
            }
        )
        limit = format_percent(self.max_outlier_rate)
        summary = (
            f"{outlier_count} of {len(values)} numeric {self.column} values "
            f"({format_percent(rate)}) are outside the {self.method} fences "
            f"(limit {limit})."
        )
        status = "fail" if rate > self.max_outlier_rate else "pass"
        return RuleResult(status=status, summary=summary, details=details)
