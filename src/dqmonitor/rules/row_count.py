"""Fail when a dataset shrinks unexpectedly or is below a minimum size."""

import pandas as pd

from dqmonitor.rules.base import (
    BaseRule,
    EvaluationContext,
    RuleResult,
    format_percent,
    round_rate,
)


class RowCountDropRule(BaseRule):
    """Compare the current row count with the previous result for this rule.

    The first run records a baseline and passes when ``min_rows`` is met.
    Later runs fail when ``(previous - current) / previous`` is greater than
    ``max_drop_ratio``. Growth does not fail this check.
    """

    type_name = "row_count_drop"
    allowed_params = frozenset({"max_drop_ratio", "min_rows"})

    def validate(self) -> None:
        self.max_drop_ratio = self.require_float("max_drop_ratio", minimum=0, maximum=1)
        self.min_rows = self.optional_int("min_rows", 1, minimum=0)

    def evaluate(self, df: pd.DataFrame, context: EvaluationContext) -> RuleResult:
        current = int(len(df))
        previous = _previous_row_count(context.previous_details)
        drop_ratio = _drop_ratio(previous, current)
        details = {
            "row_count": current,
            "previous_row_count": previous,
            "drop_ratio": None if drop_ratio is None else round_rate(drop_ratio),
            "max_drop_ratio": self.max_drop_ratio,
            "min_rows": self.min_rows,
        }
        problems: list[str] = []
        if current < self.min_rows:
            problems.append(
                f"Row count {current} is below the minimum of {self.min_rows}."
            )
        if drop_ratio is not None and drop_ratio > self.max_drop_ratio:
            problems.append(
                "Row count dropped from "
                f"{previous} to {current} "
                f"({format_percent(drop_ratio)}, limit {format_percent(self.max_drop_ratio)})."
            )
        if problems:
            return RuleResult(status="fail", summary=" ".join(problems), details=details)
        summary = _pass_summary(previous, current, drop_ratio, self.max_drop_ratio)
        return RuleResult(status="pass", summary=summary, details=details)


def _previous_row_count(details: dict | None) -> int | None:
    if not details or "row_count" not in details:
        return None
    raw = details["row_count"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, float) and not raw.is_integer():
        return None
    return int(raw)


def _drop_ratio(previous: int | None, current: int) -> float | None:
    if previous is None:
        return None
    if previous == 0:
        return 0.0 if current == 0 else None
    return (previous - current) / previous


def _pass_summary(
    previous: int | None,
    current: int,
    drop_ratio: float | None,
    max_drop_ratio: float,
) -> str:
    if previous is None:
        return f"Recorded {current} rows. No earlier row count to compare yet."
    if drop_ratio is None or drop_ratio < 0:
        return f"Row count grew from {previous} to {current}."
    if drop_ratio == 0:
        return f"Row count stayed at {current}."
    return (
        f"Row count moved from {previous} to {current} "
        f"(drop {format_percent(drop_ratio)}, limit {format_percent(max_drop_ratio)})."
    )
