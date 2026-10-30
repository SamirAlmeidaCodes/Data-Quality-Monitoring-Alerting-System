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
    """Reload YAML on each run, then execute the checks.

    Rule and source edits apply on the next run. The database URL and the
    schedule interval stay fixed until the process restarts.
    """

    def __init__(self, config_path: Path, store: Store) -> None:
        self.config_path = Path(config_path)
        self.store = store
        self._lock = threading.Lock()
        self._cache: tuple[CompiledConfig, AlertRouter] | None = None
        self._reload_error: str | None = None
        self._storage_warning_emitted = False

    def load(self) -> tuple[CompiledConfig, AlertRouter]:
        try:
            compiled = load_application(self.config_path)
            self._warn_if_storage_changed(compiled)
            fresh = (compiled, build_alerter(compiled.settings.alerting))
        except Exception as exc:
            if self._cache is None:
                raise
            message = str(exc)
            if message != self._reload_error:
                logger.exception(
                    "Configuration reload failed; using the last valid configuration"
                )
                self._reload_error = message
            return self._cache
        self._reload_error = None
        self._cache = fresh
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
            compiled.settings.storage.url,
            self.store.url,
        )
        self._storage_warning_emitted = True


def execute_run(compiled: CompiledConfig, store: Store, alerter: AlertRouter) -> RunReport:
    """Evaluate the already-built rules and persist every result."""

    started = utcnow()
    loaded: dict[str, pd.DataFrame | Exception] = {}
    for source in compiled.sources:
        try:
            loaded[source.name] = load_source(source)
        except SourceError as exc:
            logger.warning("Source %s could not be read: %s", source.name, exc)
            loaded[source.name] = exc
        except Exception as exc:
            logger.exception("Source %s could not be read", source.name)
            loaded[source.name] = exc

    results: list[StoredCheck] = []
    for rule in compiled.rules:
        if not rule.enabled:
            logger.info("Skipping disabled rule %s", rule.name)
            continue
        outcome = _evaluate_rule(rule, loaded.get(rule.source), store)
        stored = store.save(
            checked_at=started,
            rule_name=rule.name,
            rule_type=rule.type_name,
            source_name=rule.source,
            status=outcome.status,
            summary=outcome.summary,
            details=outcome.details,
        )
        results.append(stored)
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


def _evaluate_rule(rule, loaded: pd.DataFrame | Exception | None, store: Store) -> RuleResult:
    started = time.perf_counter()
    frame: pd.DataFrame | None = None
    if loaded is None:
        outcome = RuleResult(
            status="error",
            summary=f"Source '{rule.source}' is not configured.",
            details={},
        )
    elif isinstance(loaded, Exception):
        outcome = RuleResult(
            status="error",
            summary=f"Source '{rule.source}' could not be read: {loaded}",
            details={"error": str(loaded)},
        )
    else:
        frame = loaded
        previous = store.latest_by_rule(rule.name)
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
            outcome = RuleResult(
                status="error",
                summary=f"Rule raised {type(exc).__name__}: {exc}",
                details={"error": str(exc)},
            )

    details = dict(outcome.details)
    details["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if frame is not None:
        details["dataset_rows"] = int(len(frame))
    outcome.details = details
    return outcome
