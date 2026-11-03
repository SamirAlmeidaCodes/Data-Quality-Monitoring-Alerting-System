"""FastAPI application: status, history, a manual run, and the dashboard."""

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field

from dqmonitor import __version__
from dqmonitor.config import load_application
from dqmonitor.engine import CheckEngine
from dqmonitor.logging_config import configure_logging
from dqmonitor.reporting import (
    attention_message,
    check_to_dict,
    format_ts,
    overall_status,
    pending_check,
    summarize_counts,
)
from dqmonitor.scheduler import start_scheduler
from dqmonitor.api.filters import parse_history_filters
from dqmonitor.storage import Store, utcnow

logger = logging.getLogger("dqmonitor.api")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_SOURCE_LABELS = {"csv_folder": "CSV folder", "sqlite": "SQLite"}

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def _display_ts(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("T", " ").replace("+00:00", " UTC")


def _pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


_env.filters["display_ts"] = _display_ts
_env.filters["pretty_json"] = _pretty_json


class CheckPayload(BaseModel):
    id: int | None = None
    rule_name: str
    rule_type: str
    source_name: str
    status: str
    checked_at: str | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class SchedulePayload(BaseModel):
    label: str
    interval_minutes: int
    interval_seconds: int | None
    run_on_startup: bool


class StatusResponse(BaseModel):
    generated_at: str
    overall_status: str
    schedule: SchedulePayload
    checks: list[CheckPayload]


class HistoryResponse(BaseModel):
    count: int
    limit: int
    offset: int
    results: list[CheckPayload]


def create_app(config_path: str | Path, *, start_scheduler_on_boot: bool = True) -> FastAPI:
    """Build the API. The lifespan opens the database and starts the scheduler."""

    config_file = Path(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging("INFO")
        scheduler = None
        store: Store | None = None
        try:
            compiled = load_application(config_file)
            configure_logging(compiled.settings.logging.level)
            store = Store(compiled.settings.storage.url)
            store.create_tables()
            engine = CheckEngine(config_file, store)
            app.state.engine = engine
            if compiled.settings.schedule.run_on_startup:
                engine.run()
            else:
                engine.load()
            if start_scheduler_on_boot:
                scheduler = start_scheduler(engine, compiled.settings.schedule)
            app.state.scheduler = scheduler
            logger.info(
                "Data quality monitor ready (%s, %s)",
                config_file,
                compiled.settings.schedule.label(),
            )
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            if store is not None:
                store.dispose()

    app = FastAPI(
        title="Data Quality Monitor",
        summary="Scheduled data quality checks, stored results, and alerts.",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/", include_in_schema=False)
    def dashboard(
        request: Request,
        rule_name: str | None = None,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> HTMLResponse:
        checks, compiled = _current_checks(request)
        schedule_label = compiled.settings.schedule.label()
        source_line = _source_line(compiled)
        filter_error = None
        try:
            filters = parse_history_filters(
                rule_name=rule_name,
                status=status,
                source=source,
                since=None,
                until=None,
                limit=limit,
                offset=0,
            )
        except ValueError as exc:
            filter_error = str(exc)
            filters = parse_history_filters(
                rule_name=None,
                status=None,
                source=None,
                since=None,
                until=None,
                limit=50,
                offset=0,
            )
        history = request.app.state.engine.store.history(
            rule_name=filters.rule_name,
            status=filters.status,
            source_name=filters.source_name,
            limit=filters.limit,
            offset=filters.offset,
        )
        failing = [check for check in checks if check["status"] in {"fail", "error"}]
        html = _env.get_template("dashboard.html").render(
            version=__version__,
            overall_status=overall_status([check["status"] for check in checks]),
            overall_message=summarize_counts([check["status"] for check in checks]),
            attention_message=attention_message(checks),
            schedule_label=schedule_label,
            source_line=source_line,
            generated_at=_display_ts(format_ts(utcnow())),
            last_run=_display_ts(_latest_timestamp(checks)),
            checks=checks,
            failing=failing,
            history=[check_to_dict(row) for row in history],
            filters={
                "rule_name": filters.rule_name or "",
                "status": filters.status or "",
                "source": filters.source_name or "",
                "limit": filters.limit,
            },
            filter_error=filter_error,
        )
        return HTMLResponse(html)

    @app.get("/quality/status", response_model=StatusResponse)
    def quality_status(request: Request) -> dict[str, Any]:
        """Latest status of each enabled check."""

        checks, compiled = _current_checks(request)
        schedule = compiled.settings.schedule
        schedule_label = schedule.label()
        return {
            "generated_at": format_ts(utcnow()),
            "overall_status": overall_status([check["status"] for check in checks]),
            "schedule": {
                "label": schedule_label,
                "interval_minutes": schedule.interval_minutes,
                "interval_seconds": schedule.interval_seconds,
                "run_on_startup": schedule.run_on_startup,
            },
            "checks": checks,
        }

    @app.get("/quality/history", response_model=HistoryResponse)
    def quality_history(
        request: Request,
        rule_name: str | None = None,
        status: str | None = None,
        source: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Historical check results, newest first.

        ``count`` is the number of rows in this page, after ``limit`` and ``offset``.
        ``since`` and ``until`` are ISO 8601 timestamps. A value without a
        timezone is treated as UTC.
        """

        try:
            filters = parse_history_filters(
                rule_name=rule_name,
                status=status,
                source=source,
                since=since,
                until=until,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows = request.app.state.engine.store.history(
            rule_name=filters.rule_name,
            status=filters.status,
            source_name=filters.source_name,
            since=filters.since,
            until=filters.until,
            limit=filters.limit,
            offset=filters.offset,
        )
        results = [check_to_dict(row) for row in rows]
        return {
            "count": len(results),
            "limit": filters.limit,
            "offset": filters.offset,
            "results": results,
        }

    @app.post("/quality/run")
    def run_checks(request: Request):
        """Run every enabled check now.

        Browsers are redirected back to the dashboard. Other clients receive JSON.
        """

        report = request.app.state.engine.run()
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/", status_code=303)
        return JSONResponse(
            content={
                "started_at": format_ts(report.started_at),
                "finished_at": format_ts(report.finished_at),
                "counts": report.counts(),
                "results": [check_to_dict(item) for item in report.results],
            }
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    return app


def _current_checks(request: Request):
    compiled, _alerter = request.app.state.engine.load()
    store: Store = request.app.state.engine.store
    enabled = [rule for rule in compiled.rules if rule.enabled]
    latest = store.latest_for_rules([rule.name for rule in enabled])
    checks: list[dict[str, Any]] = []
    for rule in enabled:
        stored = latest.get(rule.name)
        if stored is None:
            checks.append(pending_check(rule.name, rule.type_name, rule.source))
        else:
            checks.append(check_to_dict(stored))
    return checks, compiled


def _source_line(compiled) -> str:
    names = [
        f"{source.name} ({_SOURCE_LABELS.get(source.type, source.type)})"
        for source in compiled.sources
    ]
    return ", ".join(names) if names else "No sources configured"


def _latest_timestamp(checks: list[dict[str, Any]]) -> str | None:
    stamps = [check["checked_at"] for check in checks if check.get("checked_at")]
    return max(stamps) if stamps else None
