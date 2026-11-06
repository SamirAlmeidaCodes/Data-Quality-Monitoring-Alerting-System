"""Query-parameter parsing shared by the history API and the dashboard."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

_MAX_NAME = 200
_MAX_OFFSET = 100_000
_MAX_TIMESTAMP = 40


class HistoryFilters(BaseModel):
    """Validated history query. Strings are already stripped."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_name: str | None = None
    status: str | None = None
    source_name: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int
    offset: int

    @field_validator("rule_name", "source_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > _MAX_NAME:
            raise ValueError("filter values must be at most 200 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("filter values must not contain control characters")
        return value

    @field_validator("status")
    @classmethod
    def _status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in {"pass", "fail", "error"}:
            raise ValueError("status must be one of: pass, fail, error")
        return value

    @field_validator("limit")
    @classmethod
    def _limit(cls, value: int) -> int:
        if value < 1 or value > 1000:
            raise ValueError("limit must be between 1 and 1000")
        return value

    @field_validator("offset")
    @classmethod
    def _offset(cls, value: int) -> int:
        if value < 0:
            raise ValueError("offset must be 0 or greater")
        if value > _MAX_OFFSET:
            raise ValueError("offset must be at most 100000")
        return value

    @model_validator(mode="after")
    def _order(self) -> "HistoryFilters":
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since must be earlier than or equal to until")
        return self


def parse_history_filters(
    *,
    rule_name: str | None,
    status: str | None,
    source: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    offset: int,
) -> HistoryFilters:
    """Validate history filters. Raise ValueError with a message safe to show."""

    try:
        return HistoryFilters(
            rule_name=_blank(rule_name),
            status=_blank(status),
            source_name=_blank(source),
            since=_parse_timestamp(since, "since"),
            until=_parse_timestamp(until, "until"),
            limit=limit,
            offset=offset,
        )
    except ValidationError as exc:
        raise ValueError(_validation_message(exc)) from exc


def _validation_message(exc: ValidationError) -> str:
    message = exc.errors()[0]["msg"]
    prefix = "Value error, "
    if message.startswith(prefix):
        return message[len(prefix) :]
    return message


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_timestamp(value: str | None, field: str) -> datetime | None:
    raw = _blank(value)
    if raw is None:
        return None
    if len(raw) > _MAX_TIMESTAMP:
        raise ValueError(f"{field} must be an ISO 8601 timestamp")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
