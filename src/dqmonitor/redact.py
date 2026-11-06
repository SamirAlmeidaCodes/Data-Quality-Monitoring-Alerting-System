"""Redact credentials before they are logged or returned to clients."""

import logging
import re

_URL_USERINFO = re.compile(r"(://[^/\s:@]+):[^@\s/]+@")
_BEARER = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._\-+/=]+")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api_key|apikey|access_key)"
    r"(\s*[=:]\s*)([^\s,;]+)"
)
_ABSOLUTE_PATH = re.compile(r"(?:/[\w.\-@+]+){2,}")

_installed = False


def redact(value: str) -> str:
    """Hide URL passwords, bearer tokens, and secret assignments."""

    text = _URL_USERINFO.sub(r"\1:***@", value)
    text = _BEARER.sub(r"\1***", text)
    return _ASSIGNMENT.sub(r"\1\2***", text)


def public_error_message(exc: BaseException) -> str:
    """Short error text safe to store and return from the API."""

    text = redact(" ".join(str(exc).split()))
    text = _ABSOLUTE_PATH.sub("[path]", text)
    if len(text) > 240:
        text = text[:240] + "..."
    return text or type(exc).__name__


def install_redaction() -> None:
    """Redact secrets in log messages and traceback text. Safe to call twice."""

    global _installed
    if _installed:
        return
    _installed = True
    previous_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous_factory(*args, **kwargs)
        original = record.getMessage

        def get_message() -> str:
            return redact(original())

        record.getMessage = get_message  # type: ignore[method-assign]
        return record

    logging.setLogRecordFactory(factory)
    original_format = logging.Formatter.formatException

    def format_exception(self, exc_info):
        return redact(original_format(self, exc_info))

    logging.Formatter.formatException = format_exception  # type: ignore[method-assign]
