"""Built-in data quality rules and the type registry."""

from dqmonitor.errors import ConfigError
from dqmonitor.rules.base import BaseRule, EvaluationContext, RuleResult
from dqmonitor.rules.duplicates import DuplicateSpikeRule
from dqmonitor.rules.null_rate import NullRateRule
from dqmonitor.rules.outliers import StatisticalOutliersRule
from dqmonitor.rules.row_count import RowCountDropRule
from dqmonitor.rules.schema import SchemaConsistencyRule

REGISTRY: dict[str, type[BaseRule]] = {
    NullRateRule.type_name: NullRateRule,
    RowCountDropRule.type_name: RowCountDropRule,
    DuplicateSpikeRule.type_name: DuplicateSpikeRule,
    StatisticalOutliersRule.type_name: StatisticalOutliersRule,
    SchemaConsistencyRule.type_name: SchemaConsistencyRule,
}


def get_rule_class(type_name: str) -> type[BaseRule]:
    """Return the rule class for a YAML ``type`` value."""

    try:
        return REGISTRY[type_name]
    except KeyError as exc:
        known = ", ".join(sorted(REGISTRY))
        raise ConfigError(f"Unknown rule type '{type_name}'. Known types: {known}.") from exc


__all__ = [
    "REGISTRY",
    "BaseRule",
    "DuplicateSpikeRule",
    "EvaluationContext",
    "NullRateRule",
    "RowCountDropRule",
    "RuleResult",
    "SchemaConsistencyRule",
    "StatisticalOutliersRule",
    "get_rule_class",
]
