"""APScheduler wiring for the check run."""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from dqmonitor.config_models import ScheduleConfig
from dqmonitor.engine import CheckEngine

logger = logging.getLogger("dqmonitor.scheduler")

JOB_ID = "dq-checks"


def _run_scheduled(engine: CheckEngine) -> None:
    try:
        engine.run()
    except Exception:
        logger.exception("Scheduled check run failed")


def build_scheduler(engine: CheckEngine, schedule: ScheduleConfig) -> BackgroundScheduler:
    """Create the scheduler without starting it.

    The first scheduled fire is one full interval from now. Startup runs are
    handled separately so the process does not check the data twice at boot.
    """

    interval = timedelta(**schedule.trigger_kwargs())
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_scheduled,
        trigger=IntervalTrigger(timezone="UTC", **schedule.trigger_kwargs()),
        args=[engine],
        id=JOB_ID,
        name="data quality checks",
        next_run_time=datetime.now(timezone.utc) + interval,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(int(interval.total_seconds()), 1),
        replace_existing=True,
    )
    return scheduler


def start_scheduler(engine: CheckEngine, schedule: ScheduleConfig) -> BackgroundScheduler:
    scheduler = build_scheduler(engine, schedule)
    scheduler.start()
    logger.info("Scheduled data quality checks (%s)", schedule.label())
    return scheduler
