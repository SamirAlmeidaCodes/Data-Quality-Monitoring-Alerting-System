"""Stdout logging for the monitor process."""

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE = "%Y-%m-%dT%H:%M:%S"


def configure_logging(level: str) -> None:
    """Send monitor logs to stdout at the requested level.

    Uvicorn may already have installed a handler. Lowering the root level
    keeps application INFO logs visible without replacing that handler.
    """

    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)
    logging.getLogger("dqmonitor").setLevel(numeric)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE))
        root.addHandler(handler)
