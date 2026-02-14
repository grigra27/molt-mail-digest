import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def make_scheduler(timezone: str) -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone=timezone)


def add_digest_jobs(scheduler: AsyncIOScheduler, hours: list[int], job_func):
    # weekdays: Mon-Fri
    for h in hours:
        trigger = CronTrigger(day_of_week="mon-fri", hour=h, minute=0)
        scheduler.add_job(
            job_func,
            trigger=trigger,
            kwargs={"run_hour": h},
            id=f"digest_{h:02d}00",
            replace_existing=True,
        )
        logger.info("Scheduled digest job at %02d:00 Mon-Fri", h)
