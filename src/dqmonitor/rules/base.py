"""Shared types and parameter checks for data quality rules."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from dqmonitor.errors import ConfigError


@dataclass
class EvaluationContext:
    """Facts a rule can use besides the current dataframe."""

    source_name: str
    previous_details: dict[str, Any] | None = None
    file_schemas: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.previous_details is not None:
            self.previous_details = dict(self.previous_details)
        if self.file_schemas is not None:
            self.file_schemas = [dict(item) for item in self.file_schemas]


@dataclass
class RuleResult:
    """Outcome of one rule against one dataset."""

    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"pass", "fail", "error"}:
            raise ValueError(f"Invalid rule status '{self.status}'")


def format_percent(rate: float) -> str:
    """Format a 0-1 rate as a percentage with one decimal place."""

    return f"{rate * 100:.1f}%"


def round_rate(rate: float) -> float:
    return round(float(rate), 6)


class BaseRule(ABC):
    """A configurable check.

    Subclasses declare ``type_name`` and ``allowed_params``. Anything else in
    the YAML entry is rejected so typos do not silently do nothing.
    """

    type_name: str
    allowed_params: frozenset[str] = frozenset()

    def __init__(
        self,
        name: str,
        source: str,
        enabled: bool,
        params: dict[str, Any],
    ) -> None:
        self.name = name
        self.source = source
        self.enabled = enabled
        self.params = dict(params)
        unknown = sorted(set(self.params) - set(self.allowed_params))
        if unknown:
            joined = ", ".join(unknown)
            raise ConfigError(
                f"Rule '{self.name}' ({self.type_name}) has unknown parameters: {joined}"
            )
        self.validate()

    @abstractmethod
    def validate(self) -> None:
        """Check parameters and store them as typed attributes."""

    @abstractmethod
    def evaluate(self, df: Any, context: EvaluationContext) -> RuleResult:
        """Run the check. ``df`` is a pandas DataFrame."""

    def require_float(
        self,
        key: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        if key not in self.params:
            raise ConfigError(f"Rule '{self.name}' is missing required parameter '{key}'")
        return self._as_float(key, self.params[key], minimum=minimum, maximum=maximum)

    def optional_float(
        self,
        key: str,
        default: float,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        if key not in self.params or self.params[key] is None:
            return default
        return self._as_float(key, self.params[key], minimum=minimum, maximum=maximum)

    def optional_int(self, key: str, default: int, *, minimum: int | None = None) -> int:
        if key not in self.params or self.params[key] is None:
            value = default
        else:
            value = self._as_int(key, self.params[key])
        if minimum is not None and value < minimum:
            raise ConfigError(
                f"Rule '{self.name}' parameter '{key}' must be at least {minimum}"
            )
        return value

    def optional_bool(self, key: str, default: bool) -> bool:
        if key not in self.params or self.params[key] is None:
            return default
        value = self.params[key]
        if not isinstance(value, bool):
            raise ConfigError(f"Rule '{self.name}' parameter '{key}' must be true or false")
        return value

    def require_str(self, key: str) -> str:
        if key not in self.params:
            raise ConfigError(f"Rule '{self.name}' is missing required parameter '{key}'")
        return self._as_str(key, self.params[key])

    def optional_str(
        self,
        key: str,
        default: str,
        *,
        choices: set[str] | None = None,
    ) -> str:
        if key not in self.params or self.params[key] is None:
            value = default
        else:
            value = self._as_str(key, self.params[key])
        if choices is not None and value not in choices:
            options = ", ".join(sorted(choices))
            raise ConfigError(
                f"Rule '{self.name}' parameter '{key}' must be one of: {options}"
            )
        return value

    def require_str_list(self, key: str) -> list[str]:
        if key not in self.params:
            raise ConfigError(f"Rule '{self.name}' is missing required parameter '{key}'")
        return self._as_str_list(key, self.params[key])

    def optional_str_list(self, key: str) -> list[str] | None:
        if key not in self.params or self.params[key] is None:
            return None
        return self._as_str_list(key, self.params[key])

    def _as_float(
        self,
        key: str,
        raw: Any,
        *,
        minimum: float | None,
        maximum: float | None,
    ) -> float:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ConfigError(f"Rule '{self.name}' parameter '{key}' must be a number")
        value = float(raw)
        if minimum is not None and value < minimum:
            raise ConfigError(
                f"Rule '{self.name}' parameter '{key}' must be at least {minimum}"
            )
        if maximum is not None and value > maximum:
            raise ConfigError(
                f"Rule '{self.name}' parameter '{key}' must be at most {maximum}"
            )
        return value

    def _as_int(self, key: str, raw: Any) -> int:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ConfigError(f"Rule '{self.name}' parameter '{key}' must be an integer")
        if isinstance(raw, float) and not raw.is_integer():
            raise ConfigError(f"Rule '{self.name}' parameter '{key}' must be an integer")
        return int(raw)

    def _as_str(self, key: str, raw: Any) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigError(
                f"Rule '{self.name}' parameter '{key}' must be a non-empty string"
            )
        return raw.strip()

    def _as_str_list(self, key: str, raw: Any) -> list[str]:
        if not isinstance(raw, list) or not raw:
            raise ConfigError(
                f"Rule '{self.name}' parameter '{key}' must be a non-empty list of strings"
            )
        values: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(
                    f"Rule '{self.name}' parameter '{key}' must be a non-empty list of strings"
                )
            values.append(item.strip())
        if len(values) != len(set(values)):
            raise ConfigError(f"Rule '{self.name}' parameter '{key}' contains duplicates")
        return values
