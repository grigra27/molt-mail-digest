import logging
from datetime import datetime

from telethon import TelegramClient
from telethon.sessions import StringSession

from city_extract import extract_spb_vacancies
from config import Config
from db import get_tg_source_last_id, set_tg_source_last_id

logger = logging.getLogger(__name__)


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "unknown-date"
    return dt.strftime("%Y-%m-%d %H:%M")


async def run_spb_jobs_digest(cfg: Config) -> tuple[str, int]:
    if not cfg.telegram_user_enabled:
        return "Источник Telegram-каналов отключён (TELEGRAM_USER_ENABLED=0).", 0

    client = TelegramClient(StringSession(cfg.telegram_user_session), cfg.telegram_user_api_id, cfg.telegram_user_api_hash)

    lines: list[str] = ["Вакансии Санкт-Петербурга из Telegram-каналов:"]
    matched_posts = 0

    async with client:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram user session is not authorized. Recreate TELEGRAM_USER_SESSION.")

        for channel_ref in cfg.telegram_source_channels:
            entity = await client.get_entity(channel_ref)
            channel_id = str(entity.id)
            last_id = get_tg_source_last_id(channel_id)

            msgs = await client.get_messages(entity, limit=cfg.telegram_source_fetch_limit, min_id=last_id)
            if not msgs:
                continue

            max_seen = last_id
            new_hits = 0

            for msg in reversed(msgs):
                if not msg or not getattr(msg, "id", None):
                    continue
                if msg.id > max_seen:
                    max_seen = msg.id

                text = (msg.message or "").strip()
                if not text:
                    continue

                vacancies = extract_spb_vacancies(text, banned_keywords=cfg.telegram_vacancy_banned_words)
                if not vacancies:
                    continue

                matched_posts += 1
                new_hits += 1
                title = getattr(entity, "title", None) or channel_ref
                company = vacancies[0].company if vacancies else ""
                header = f"\nКанал: {title} | пост #{msg.id} | {_fmt_dt(msg.date)}"
                if company:
                    header += f" | Компания: {company}"
                lines.append(header)
                for idx, item in enumerate(vacancies, start=1):
                    lines.append(f"{idx}. {item.title}")
                    lines.append(f"   {item.link}")

            set_tg_source_last_id(channel_id, max_seen)
            logger.info("Channel %s processed: fetched=%s, matched=%s, last_id=%s", channel_ref, len(msgs), new_hits, max_seen)

    if matched_posts == 0:
        return "В новых постах по выбранным каналам вакансий СПб не найдено.", 0

    return "\n".join(lines), matched_posts
