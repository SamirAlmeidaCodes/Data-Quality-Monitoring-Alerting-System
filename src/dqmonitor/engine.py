"""Run every enabled rule once and store the results."""

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from dqmonitor.alerting import AlertRouter, build_alerter
from dqmonitor.config import CompiledConfig, load_application
from dqmonitor.errors import SourceError
from dqmonitor.redact import public_error_message, redact
from dqmonitor.rules.base import EvaluationContext, RuleResult
from dqmonitor.sources import load_source
from dqmonitor.storage import Store, StoredCheck, utcnow

logger = logging.getLogger("dqmonitor.engine")


@dataclass
class RunReport:
    started_at: datetime
    finished_at: datetime
    results: list[StoredCheck]

    def counts(self) -> dict[str, int]:
        tally = Counter(item.status for item in self.results)
        return {
            "total": len(self.results),
            "pass": tally["pass"],
            "fail": tally["fail"],
            "error": tally["error"],
        }


class CheckEngine:
    """Reload YAML when the config file changes, then execute the checks.

    Rule and source edits apply on the next run. The database URL and the
    schedule interval stay fixed until the process restarts. A parsed config
    is reused until the file's modification time or size changes.
    """

    def __init__(self, config_path: Path, store: Store) -> None:
        self.config_path = Path(config_path)
        self.store = store
        self._lock = threading.Lock()
        self._snapshot: tuple[tuple[int, int] | None, CompiledConfig, AlertRouter] | None = None
        self._reload_error: str | None = None
        self._storage_warning_emitted = False

    def load(self) -> tuple[CompiledConfig, AlertRouter]:
        stamp = _config_stamp(self.config_path)
        snapshot = self._snapshot
        if snapshot is not None and stamp is not None and snapshot[0] == stamp:
            return snapshot[1], snapshot[2]
        try:
            compiled = load_application(self.config_path)
            self._warn_if_storage_changed(compiled)
            fresh = (compiled, build_alerter(compiled.settings.alerting))
        except Exception as exc:
            if snapshot is None:
                raise
            message = str(exc)
            if message != self._reload_error:
                logger.exception(
                    "Configuration reload failed; using the last valid configuration"
                )
                self._reload_error = message
            return snapshot[1], snapshot[2]
        self._reload_error = None
        after = _config_stamp(self.config_path)
        # A stamp that moved during the read is not cached, so the next call reloads.
        self._snapshot = (after if after == stamp else None, fresh[0], fresh[1])
        return fresh

    def run(self) -> RunReport:
        with self._lock:
            compiled, alerter = self.load()
            return execute_run(compiled, self.store, alerter)

    def _warn_if_storage_changed(self, compiled: CompiledConfig) -> None:
        if compiled.settings.storage.url == self.store.url or self._storage_warning_emitted:
            return
        logger.warning(
            "Config storage url is %s but this process is using %s. Restart to switch databases.",
            redact(compiled.settings.storage.url),
            redact(self.store.url),
        )
        self._storage_warning_emitted = True


def execute_run(compiled: CompiledConfig, store: Store, alerter: AlertRouter) -> RunReport:
    """Evaluate the already-built rules and persist every result in one commit."""

    started = utcnow()
    loaded: dict[str, pd.DataFrame | Exception] = {}
    for source in compiled.sources:
        try:
            loaded[source.name] = load_source(source)
        except SourceError as exc:
            logger.warning("Source %s could not be read: %s", source.name, redact(str(exc)))
            loaded[source.name] = exc
        except Exception as exc:
            logger.exception("Source %s could not be read", source.name)
            loaded[source.name] = exc

    enabled = []
    for rule in compiled.rules:
        if not rule.enabled:
            logger.info("Skipping disabled rule %s", rule.name)
            continue
        enabled.append(rule)

    previous = store.latest_for_rules([rule.name for rule in enabled])
    outcomes = [
        (rule, _evaluate_rule(rule, loaded.get(rule.source), previous.get(rule.name)))
        for rule in enabled
    ]
    results = store.save_many(
        [
            {
                "checked_at": started,
                "rule_name": rule.name,
                "rule_type": rule.type_name,
                "source_name": rule.source,
                "status": outcome.status,
                "summary": outcome.summary,
                "details": outcome.details,
            }
            for rule, outcome in outcomes
        ]
    )
    for stored in results:
        logger.info(
            "Check %s status=%s source=%s summary=%s",
            stored.rule_name,
            stored.status,
            stored.source_name,
            stored.summary,
        )
        alerter.send(stored)

    finished = utcnow()
    tally = Counter(item.status for item in results)
    logger.info(
        "Check run finished: %s checks, %s pass, %s fail, %s error",
        len(results),
        tally["pass"],
        tally["fail"],
        tally["error"],
    )
    return RunReport(started_at=started, finished_at=finished, results=results)


def _config_stamp(path: Path) -> tuple[int, int] | None:
    """Modification time and size, or None when the file cannot be stated."""

    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _evaluate_rule(
    rule,
    loaded: pd.DataFrame | Exception | None,
    previous: StoredCheck | None,
) -> RuleResult:
    started = time.perf_counter()
    frame: pd.DataFrame | None = None
    if loaded is None:
        outcome = RuleResult(
            status="error",
            summary=f"Source '{rule.source}' is not configured.",
            details={},
        )
    elif isinstance(loaded, Exception):
        safe = public_error_message(loaded)
        outcome = RuleResult(
            status="error",
            summary=f"Source '{rule.source}' could not be read: {safe}",
            details={"error_type": type(loaded).__name__, "error": safe},
        )
    else:
        frame = loaded
        meta = frame.attrs.get("dq_meta") or {}
        context = EvaluationContext(
            source_name=rule.source,
            previous_details=None if previous is None else previous.details,
            file_schemas=meta.get("files"),
        )
        try:
            outcome = rule.evaluate(frame, context)
        except Exception as exc:
            logger.exception("Rule %s raised an exception", rule.name)
            safe = public_error_message(exc)
            outcome = RuleResult(
                status="error",
                summary=f"Rule raised {type(exc).__name__}: {safe}",
                details={"error_type": type(exc).__name__, "error": safe},
            )

    details = dict(outcome.details)
    details["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if frame is not None:
        details["dataset_rows"] = int(len(frame))
    outcome.details = details
    return outcome
