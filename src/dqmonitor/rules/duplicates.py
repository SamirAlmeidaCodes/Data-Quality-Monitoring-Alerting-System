"""Fail when duplicate keys spike above a configured rate."""

import pandas as pd

from dqmonitor.rules.base import (
    BaseRule,
    EvaluationContext,
    RuleResult,
    format_percent,
    round_rate,
)


class DuplicateSpikeRule(BaseRule):
    """Measure extra copies of ``key_columns``.

    ``duplicate_rate`` is ``(rows - distinct keys) / rows``. Rows whose key
    is null are duplicates of each other, because pandas treats missing keys
    as equal. The check fails when the rate is greater than
    ``max_duplicate_rate``.
    """

    type_name = "duplicate_spike"
    allowed_params = frozenset({"key_columns", "max_duplicate_rate"})

    def validate(self) -> None:
        self.key_columns = self.require_str_list("key_columns")
        self.max_duplicate_rate = self.require_float(
            "max_duplicate_rate", minimum=0, maximum=1
        )

    def evaluate(self, df: pd.DataFrame, context: EvaluationContext) -> RuleResult:
        del context
        missing = [column for column in self.key_columns if column not in df.columns]
        key_label = ", ".join(self.key_columns)
        if missing:
            return RuleResult(
                status="fail",
                summary="Missing key columns: " + ", ".join(missing),
                details={
                    "key_columns": self.key_columns,
                    "missing_columns": missing,
                    "max_duplicate_rate": self.max_duplicate_rate,
                },
            )
        if df.empty:
            return RuleResult(
                status="fail",
                summary="Dataset is empty, so the duplicate rate is undefined.",
                details={
                    "row_count": 0,
                    "key_columns": self.key_columns,
                    "max_duplicate_rate": self.max_duplicate_rate,
                },
            )

        unique_keys = int(df.drop_duplicates(subset=self.key_columns).shape[0])
        duplicate_rows = int(len(df) - unique_keys)
        rate = round_rate(duplicate_rows / len(df))
        details = {
            "row_count": int(len(df)),
            "unique_keys": unique_keys,
            "duplicate_rows": duplicate_rows,
            "duplicate_rate": rate,
            "max_duplicate_rate": self.max_duplicate_rate,
            "key_columns": self.key_columns,
        }
        limit = format_percent(self.max_duplicate_rate)
        measured = format_percent(rate)
        if rate > self.max_duplicate_rate:
            summary = (
                f"Duplicate rate on ({key_label}) is {measured} (limit {limit}). "
                f"{duplicate_rows} extra rows out of {len(df)}."
            )
            return RuleResult(status="fail", summary=summary, details=details)
        summary = f"Duplicate rate on ({key_label}) is {measured} (limit {limit})."
        return RuleResult(status="pass", summary=summary, details=details)
