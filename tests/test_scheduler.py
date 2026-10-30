"""Scheduler interval wiring."""

from datetime import timedelta
from types import SimpleNamespace

from dqmonitor.config_models import ScheduleConfig
from dqmonitor.scheduler import JOB_ID, build_scheduler


def test_default_interval_is_one_hour():
    scheduler = build_scheduler(SimpleNamespace(), ScheduleConfig())
    try:
        job = scheduler.get_job(JOB_ID)
        assert job is not None
        assert job.trigger.interval == timedelta(hours=1)
        assert job.max_instances == 1
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_interval_seconds_override_minutes():
    schedule = ScheduleConfig(interval_minutes=60, interval_seconds=45)
    scheduler = build_scheduler(SimpleNamespace(), schedule)
    try:
        job = scheduler.get_job(JOB_ID)
        assert job.trigger.interval == timedelta(seconds=45)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
