"""Pydantic models for the YAML configuration."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    """Reject unknown keys so a typo fails at startup."""

    model_config = ConfigDict(extra="forbid")


def _stripped(value: str) -> str:
    value = value.strip()
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
    url: str = "sqlite:///data/monitor.db"

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
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    username: str = ""
    password_env: str = "DQ_SMTP_PASSWORD"
    from_addr: str = "dq-monitor@localhost"
    to_addrs: list[str] = Field(default_factory=list)
    use_tls: bool = True
    use_ssl: bool = False


class WebhookConfig(StrictModel):
    enabled: bool = False
    url: str = ""
    timeout_seconds: float = Field(default=10, gt=0)
    headers: dict[str, str] = Field(default_factory=dict)
    bearer_token_env: str = ""


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

    name: str
    type: Literal["csv_folder"]
    path: str
    pattern: str = "*.csv"
    mode: Literal["concat", "latest"] = "concat"
    encoding: str = "utf-8"

    @field_validator("name", "path", "pattern", "encoding")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return _stripped(value)


class SqliteSourceConfig(StrictModel):
    """A table in a SQLite database.

    ``url`` uses SQLAlchemy form, for example ``sqlite:///data/warehouse.db``.
    """

    name: str
    type: Literal["sqlite"]
    url: str
    table: str

    @field_validator("name", "url", "table")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        return _stripped(value)


SourceConfig = Annotated[
    CsvFolderSourceConfig | SqliteSourceConfig,
    Field(discriminator="type"),
]


class RuleConfig(BaseModel):
    """One check. Fields other than the identity fields are rule parameters."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    source: str
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
