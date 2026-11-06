"""Stdout logging for the monitor process."""

import logging
import os
import sys

from dqmonitor.redact import install_redaction

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE = "%Y-%m-%dT%H:%M:%S"
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(level: str) -> None:
    """Send monitor logs to stdout at the requested level.

    Uvicorn may already have installed a handler. Lowering the root level
    keeps application INFO logs visible without replacing that handler.
    DEBUG is forced to INFO when DQ_ENV is production. httpx is kept at
    WARNING so request logs cannot include an Authorization header.
    """

    install_redaction()
    normalized = level.strip().upper()
    if normalized not in _LEVELS:
        normalized = "INFO"
    if _is_production() and normalized == "DEBUG":
        normalized = "INFO"
    numeric = getattr(logging, normalized, logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)
    logging.getLogger("dqmonitor").setLevel(numeric)
    logging.getLogger("httpx").setLevel(max(numeric, logging.WARNING))
    logging.getLogger("httpcore").setLevel(max(numeric, logging.WARNING))
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE))
        root.addHandler(handler)


def _is_production() -> bool:
    return os.environ.get("DQ_ENV", "").strip().lower() in {"prod", "production"}
