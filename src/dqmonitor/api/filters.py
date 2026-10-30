"""Query-parameter parsing shared by the history API and the dashboard."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class HistoryFilters:
    rule_name: str | None
    status: str | None
    source_name: str | None
    since: datetime | None
    until: datetime | None
    limit: int
    offset: int


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

    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    if offset < 0:
        raise ValueError("offset must be 0 or greater")

    status_value = _blank(status)
    if status_value is not None and status_value not in {"pass", "fail", "error"}:
        raise ValueError("status must be one of: pass, fail, error")

    since_at = _parse_timestamp(since, "since")
    until_at = _parse_timestamp(until, "until")
    if since_at is not None and until_at is not None and since_at > until_at:
        raise ValueError("since must be earlier than or equal to until")

    return HistoryFilters(
        rule_name=_blank(rule_name),
        status=status_value,
        source_name=_blank(source),
        since=since_at,
        until=until_at,
        limit=limit,
        offset=offset,
    )


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_timestamp(value: str | None, field: str) -> datetime | None:
    raw = _blank(value)
    if raw is None:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
