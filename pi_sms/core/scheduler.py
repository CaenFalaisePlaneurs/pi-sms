"""Scheduling logic for the SMS inbox poll job."""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from .config import Config
from .debug import _is_debug_mode, debug_print

_POLL_JOB_ID = "poll_job"
_COUNTDOWN_JOB_ID = "countdown_log"


class MisfireWarningFilter(logging.Filter):
    """Filter to suppress APScheduler misfire warnings."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter out warnings about missed job runs (expected on slow polls)."""
        return "was missed by" not in record.getMessage()


def _configure_scheduler_logger() -> None:
    """Configure APScheduler logger to suppress misfire warnings."""
    apscheduler_logger = logging.getLogger("apscheduler")
    apscheduler_logger.addFilter(MisfireWarningFilter())
    for handler in apscheduler_logger.handlers:
        handler.addFilter(MisfireWarningFilter())
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(MisfireWarningFilter())


def _get_interval_seconds(config: Config) -> int:
    """Return the poll interval, using the debug override when DEBUG_MODE is set."""
    if _is_debug_mode() and config.debug is not None:
        return config.debug.poll_interval_seconds
    return config.poll.interval_seconds


async def log_countdown(scheduler: AsyncIOScheduler | None) -> None:
    """Log a countdown until the next poll (debug mode only)."""
    if scheduler is None or not _is_debug_mode():
        return

    try:
        job = scheduler.get_job(_POLL_JOB_ID)
        if not job or not job.next_run_time:
            return

        now = datetime.now(UTC)
        next_run = job.next_run_time
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=UTC)
        remaining = (next_run - now).total_seconds()
        if remaining > 0.5:
            print(f"Next poll in: {int(remaining)}s", end="\r", flush=True)
        else:
            print("Next poll in: executing...", end="\r", flush=True)
    except Exception:
        pass


def start_scheduler(
    config: Config,
    poll_and_process_func: Callable[[], Awaitable[None]],
) -> AsyncIOScheduler:
    """Create and start the scheduler with the SMS inbox poll job.

    Args:
        config: Configuration object (determines the poll interval)
        poll_and_process_func: Async callable invoked on each poll

    Returns:
        The started APScheduler instance
    """
    _configure_scheduler_logger()
    scheduler = AsyncIOScheduler()
    scheduler.start()

    interval_seconds = _get_interval_seconds(config)
    debug_print(f"Poll interval: {interval_seconds}s")

    scheduler.add_job(
        poll_and_process_func,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id=_POLL_JOB_ID,
        max_instances=1,  # Prevent concurrent polls
        coalesce=True,  # Run at most once if multiple runs are missed
        misfire_grace_time=60,  # Ignore missed runs if more than 60 seconds late
    )

    if _is_debug_mode():

        async def _log_countdown_wrapper() -> None:
            await log_countdown(scheduler)

        scheduler.add_job(
            _log_countdown_wrapper,
            trigger=IntervalTrigger(seconds=1),
            id=_COUNTDOWN_JOB_ID,
            coalesce=True,
            misfire_grace_time=5,
            max_instances=1,
        )

    return scheduler
