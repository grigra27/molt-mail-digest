import asyncio
import logging
import os

from config import load_config, Config
from db import init_db, get_paused
from telegram_bot import build_app, send_to_owner
from scheduler import make_scheduler, add_digest_jobs
from digest import run_digest


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def main_async():
    cfg: Config = load_config()
    setup_logging(cfg.log_level)
    init_db()

    # Ensure TZ for APScheduler and logs
    os.environ["TZ"] = cfg.tz

    app = build_app(cfg)

    scheduler = make_scheduler(cfg.tz)

    async def scheduled_digest_job():
        if get_paused():
            logging.getLogger(__name__).info("Paused; skipping scheduled digest.")
            return
        try:
            text, total, failed = run_digest(cfg)
            await send_to_owner(app, cfg, text)
            await send_to_owner(app, cfg, f"Авто-дайджест отправлен. Писем: {total}, не обработано: {failed}.")
        except Exception as e:
            logging.getLogger(__name__).exception("Scheduled digest failed")
            await send_to_owner(app, cfg, f"Ошибка авто-дайджеста: {e}")

    add_digest_jobs(scheduler, cfg.schedule_hours, scheduled_digest_job)
    scheduler.start()

    # Start bot (long polling)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main_async())
