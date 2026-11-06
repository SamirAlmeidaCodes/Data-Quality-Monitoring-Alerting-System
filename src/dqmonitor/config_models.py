"""Pydantic models for the YAML configuration."""

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    """Reject unknown keys so a typo fails at startup."""

    model_config = ConfigDict(extra="forbid")


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SENSITIVE_HEADER = re.compile(
    r"(?i)(authorization|api[-_]?key|token|secret|password|cookie)"
)


def _no_controls(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("must not contain control characters")
    return value.strip()


def _stripped(value: str) -> str:
    value = _no_controls(value)
    if not value:
        raise ValueError("must not be empty")
    return value


class ScheduleConfig(StrictModel):
    """How often checks run.

    ``interval_seconds`` overrides ``interval_minutes`` when both are set.
    The default is every 60 minutes. Changing the interval needs a restart.
    """

    interval_minutes: int = Field(default=60, ge=1)
    interval_seconds: int | None = Field(default=None, ge=1)
    run_on_startup: bool = True

    def trigger_kwargs(self) -> dict[str, int]:
        if self.interval_seconds:
            return {"seconds": self.interval_seconds}
        return {"minutes": self.interval_minutes}

    def label(self) -> str:
        if self.interval_seconds:
            count = self.interval_seconds
            unit = "second" if count == 1 else "seconds"
            return f"Every {count} {unit}"
        count = self.interval_minutes
        unit = "minute" if count == 1 else "minutes"
        return f"Every {count} {unit}"


class StorageConfig(StrictModel):
    url: str = Field(default="sqlite:///data/monitor.db", max_length=2048)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _stripped(value)


class LoggingConfig(StrictModel):
    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        normalized = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError("must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized


class EmailConfig(StrictModel):
    enabled: bool = False
    smtp_host: str = Field(default="localhost", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(default="", max_length=320)
    password_env: str = Field(default="DQ_SMTP_PASSWORD", max_length=128)
    from_addr: str = Field(default="dq-monitor@localhost", max_length=320)
    to_addrs: list[str] = Field(default_factory=list)
    use_tls: bool = True
    use_ssl: bool = False

    @field_validator("smtp_host", "username", "from_addr")
    @classmethod
    def _text(cls, value: str) -> str:
        return _no_controls(value)

    @field_validator("password_env")
    @classmethod
    def _password_env(cls, value: str) -> str:
        value = _stripped(value)
        if not _ENV_NAME.fullmatch(value):
            raise ValueError("password_env must be an environment variable name, not the password")
        return value

    @field_validator("to_addrs")
    @classmethod
    def _recipients(cls, value: list[str]) -> list[str]:
        if len(value) > 50:
            raise ValueError("to_addrs must contain at most 50 addresses")
        cleaned = []
        for item in value:
            address = _stripped(item)
            if len(address) > 320:
                raise ValueError("email addresses must be at most 320 characters")
            cleaned.append(address)
        return cleaned


class WebhookConfig(StrictModel):
    enabled: bool = False
    url: str = Field(default="", max_length=2048)
    timeout_seconds: float = Field(default=10, gt=0, le=120)
    headers: dict[str, str] = Field(default_factory=dict)
    bearer_token_env: str = Field(default="", max_length=128)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _no_controls(value)

    @field_validator("bearer_token_env")
    @classmethod
    def _bearer_env(cls, value: str) -> str:
        value = _no_controls(value)
        if value and not _ENV_NAME.fullmatch(value):
            raise ValueError("bearer_token_env must be an environment variable name")
        return value

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 20:
            raise ValueError("webhook headers must contain at most 20 entries")
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            name = _stripped(str(key))
            header_value = _no_controls(str(item))
            if len(name) > 128 or len(header_value) > 512:
                raise ValueError("webhook header names and values are too long")
            if _SENSITIVE_HEADER.search(name):
                raise ValueError(
                    "credential headers must not be stored in config; use bearer_token_env"
                )
            cleaned[name] = header_value
        return cleaned


class AlertingConfig(StrictModel):
    console: bool = True
    on_statuses: list[str] = Field(default_factory=lambda: ["fail", "error"])
    email: EmailConfig = Field(default_factory=EmailConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)

    @field_validator("on_statuses")
    @classmethod
    def _statuses(cls, value: list[str]) -> list[str]:
        allowed = {"pass", "fail", "error"}
        invalid = [item for item in value if item not in allowed]
        if invalid:
            raise ValueError(f"unsupported statuses: {', '.join(invalid)}")
        return value


class CsvFolderSourceConfig(StrictModel):
    """A directory of CSV files, or one CSV file path.

    ``mode`` is ``concat`` (read every match) or ``latest`` (newest match).
    Paths are relative to the process working directory.
    """

    name: str = Field(max_length=200)
    type: Literal["csv_folder"]
    path: str = Field(max_length=4096)
    pattern: str = Field(default="*.csv", max_length=255)
    mode: Literal["concat", "latest"] = "concat"
    encoding: str = Field(default="utf-8", max_length=64)

    @field_validator("name", "path", "encoding")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return _stripped(value)

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, value: str) -> str:
        value = _stripped(value)
        posix = PurePosixPath(value).parts
        windows = PureWindowsPath(value).parts
        if value.startswith(("/", "\\")) or ".." in posix or ".." in windows:
            raise ValueError("pattern must stay inside the source directory")
        return value


class SqliteSourceConfig(StrictModel):
    """A table in a SQLite database.

    ``url`` uses SQLAlchemy form, for example ``sqlite:///data/warehouse.db``.
    """

    name: str = Field(max_length=200)
    type: Literal["sqlite"]
    url: str = Field(max_length=2048)
    table: str = Field(max_length=128)

    @field_validator("name", "url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return _stripped(value)

    @field_validator("table")
    @classmethod
    def _table(cls, value: str) -> str:
        value = _stripped(value)
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"table name '{value}' is not a valid identifier")
        return value


SourceConfig = Annotated[
    CsvFolderSourceConfig | SqliteSourceConfig,
    Field(discriminator="type"),
]


class RuleConfig(BaseModel):
    """One check. Fields other than the identity fields are rule parameters."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(max_length=200)
    type: str = Field(max_length=100)
    source: str = Field(max_length=200)
    enabled: bool = True

    @field_validator("name", "type", "source")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return _stripped(value)

    def parameters(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Settings(StrictModel):
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    alerting: AlertingConfig = Field(default_factory=AlertingConfig)
    sources: list[SourceConfig] = Field(default_factory=list)
    rules: list[RuleConfig] = Field(default_factory=list)
