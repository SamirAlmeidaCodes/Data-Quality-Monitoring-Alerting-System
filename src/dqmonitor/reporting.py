"""Status summaries and API payloads."""

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from dqmonitor.storage import StoredCheck

_OVERALL_RANK = {"pass": 0, "pending": 1, "error": 2, "fail": 3}


def format_ts(value: datetime | None) -> str | None:
    """Render a stored timestamp as UTC ISO 8601."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def overall_status(statuses: list[str]) -> str:
    """Pick the worst status. Fail outranks error, which outranks pending."""

    if not statuses:
        return "unknown"
    if any(status not in _OVERALL_RANK for status in statuses):
        return "unknown"
    return max(statuses, key=lambda status: _OVERALL_RANK[status])


def summarize_counts(statuses: list[str]) -> str:
    if not statuses:
        return "No checks are configured."
    counts = Counter(statuses)
    total = len(statuses)
    noun = "check" if total == 1 else "checks"
    if counts["pass"] == total:
        return f"All {total} {noun} passed."
    parts: list[str] = []
    if counts["fail"]:
        parts.append(f"{counts['fail']} failed")
    if counts["error"]:
        parts.append(f"{counts['error']} errored")
    if counts["pending"]:
        parts.append(f"{counts['pending']} not run yet")
    if counts["pass"]:
        parts.append(f"{counts['pass']} passed")
    return f"{total} {noun}: " + ", ".join(parts) + "."


def attention_message(checks: list[dict[str, Any]]) -> str | None:
    """Empty-state copy for the failing-rules panel. None when there is work to show."""

    if any(check["status"] in {"fail", "error"} for check in checks):
        return None
    if not checks:
        return "No checks are configured."
    if any(check["status"] == "pending" for check in checks):
        return (
            "No failed checks. Checks that have not run yet are listed "
            "in the current status table."
        )
    return "Every configured check passed."


def pending_check(rule_name: str, rule_type: str, source_name: str) -> dict[str, Any]:
    return {
        "id": None,
        "rule_name": rule_name,
        "rule_type": rule_type,
        "source_name": source_name,
        "status": "pending",
        "checked_at": None,
        "summary": "Not run yet.",
        "details": {},
    }


def check_to_dict(check: StoredCheck) -> dict[str, Any]:
    return {
        "id": check.id,
        "rule_name": check.rule_name,
        "rule_type": check.rule_type,
        "source_name": check.source_name,
        "status": check.status,
        "checked_at": format_ts(check.checked_at),
        "summary": check.summary,
        "details": check.details,
    }
