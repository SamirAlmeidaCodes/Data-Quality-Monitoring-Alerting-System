"""Fail when expected columns are missing from a dataset or its input files."""

import pandas as pd

from dqmonitor.rules.base import BaseRule, EvaluationContext, RuleResult


class SchemaConsistencyRule(BaseRule):
    """Require a stable set of columns.

    Extra columns pass unless ``allow_extra`` is false. When the source
    provides per-file schemas, every file must contain the expected columns.
    A column that exists only in some files still fails this check.
    """

    type_name = "schema_consistency"
    allowed_params = frozenset({"expected_columns", "case_sensitive", "allow_extra"})

    def validate(self) -> None:
        self.expected_columns = self.require_str_list("expected_columns")
        self.case_sensitive = self.optional_bool("case_sensitive", True)
        self.allow_extra = self.optional_bool("allow_extra", True)

    def evaluate(self, df: pd.DataFrame, context: EvaluationContext) -> RuleResult:
        actual = [str(column) for column in df.columns]
        missing = [
            column
            for column in self.expected_columns
            if self._norm(column) not in {self._norm(name) for name in actual}
        ]
        expected_norms = {self._norm(column) for column in self.expected_columns}
        extra = [column for column in actual if self._norm(column) not in expected_norms]

        files_missing: dict[str, list[str]] = {}
        for item in context.file_schemas or []:
            columns = [str(column) for column in item.get("columns", [])]
            present = {self._norm(column) for column in columns}
            absent = [
                column
                for column in self.expected_columns
                if self._norm(column) not in present
            ]
            if absent:
                files_missing[str(item.get("name", "input"))] = absent

        details = {
            "expected_columns": self.expected_columns,
            "actual_columns": actual,
            "missing_columns": missing,
            "extra_columns": extra,
            "files_missing_columns": files_missing,
            "case_sensitive": self.case_sensitive,
            "allow_extra": self.allow_extra,
        }
        problems: list[str] = []
        if missing and not files_missing:
            problems.append("Missing required columns: " + ", ".join(missing) + ".")
        if files_missing:
            described = ", ".join(
                f"{name} ({', '.join(columns)})" for name, columns in files_missing.items()
            )
            problems.append("Some inputs are missing required columns: " + described + ".")
        if extra and not self.allow_extra:
            problems.append("Unexpected columns: " + ", ".join(extra) + ".")
        if problems:
            return RuleResult(status="fail", summary=" ".join(problems), details=details)

        summary = f"Schema contains the {len(self.expected_columns)} expected columns."
        if extra:
            summary += " Extra columns: " + ", ".join(extra) + "."
        return RuleResult(status="pass", summary=summary, details=details)

    def _norm(self, value: str) -> str:
        text = str(value)
        return text if self.case_sensitive else text.casefold()
