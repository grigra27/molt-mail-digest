import logging
from dataclasses import dataclass
from datetime import datetime

from telethon import TelegramClient
from telethon.sessions import StringSession

from city_extract import extract_inline_hh_links_from_entities, parse_spb_vacancies
from config import Config
from db import (
    get_tg_house_last_id,
    get_tg_source_last_id,
    set_tg_house_last_id,
    set_tg_source_last_id,
)
from llm import make_client, summarize_house_chat_messages

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelRunStats:
    channel_ref: str
    channel_title: str
    fetched_posts: int
    detected_vacancies: int
    selected_vacancies: int


@dataclass(frozen=True)
class HouseChatRunStats:
    house_name: str
    chat_ref: str
    chat_title: str
    fetched_messages: int


def format_channel_stats(channel_stats: list[ChannelRunStats]) -> str:
    if not channel_stats:
        return "Каналы: нет данных."

    lines = ["Статистика по каналам:"]
    for st in channel_stats:
        lines.append(
            f"- {st.channel_title} ({st.channel_ref}): постов просмотрено {st.fetched_posts}, "
            f"вакансий отсмотрено {st.detected_vacancies}, выбрано {st.selected_vacancies}"
        )
    return "\n".join(lines)


def format_house_chat_stats(chat_stats: list[HouseChatRunStats]) -> str:
    if not chat_stats:
        return "Чаты домов: нет данных."

    lines = ["Статистика по чатам домов:"]
    for st in chat_stats:
        lines.append(
            f"- {st.house_name} ({st.chat_title}, {st.chat_ref}): сообщений просмотрено {st.fetched_messages}"
        )
    return "\n".join(lines)


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "unknown-date"
    return dt.strftime("%Y-%m-%d %H:%M")


async def run_spb_jobs_digest(cfg: Config) -> tuple[str, int, list[ChannelRunStats]]:
    if not cfg.telegram_user_enabled:
        return "Источник Telegram-каналов отключён (TELEGRAM_USER_ENABLED=0).", 0, []

    client = TelegramClient(StringSession(cfg.telegram_user_session), cfg.telegram_user_api_id, cfg.telegram_user_api_hash)

    lines: list[str] = ["Вакансии Санкт-Петербурга из Telegram-каналов:"]
    matched_posts = 0
    channel_stats: list[ChannelRunStats] = []

    async with client:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram user session is not authorized. Recreate TELEGRAM_USER_SESSION.")

        for channel_ref in cfg.telegram_source_channels:
            entity = await client.get_entity(channel_ref)
            channel_id = str(entity.id)
            channel_title = getattr(entity, "title", None) or channel_ref
            last_id = get_tg_source_last_id(channel_id)

            msgs = await client.get_messages(entity, limit=cfg.telegram_source_fetch_limit, min_id=last_id)

            max_seen = last_id
            new_hits = 0
            detected_vacancies = 0
            selected_vacancies = 0

            for msg in reversed(msgs):
                if not msg or not getattr(msg, "id", None):
                    continue
                if msg.id > max_seen:
                    max_seen = msg.id

                text = (msg.message or "").strip()
                if not text:
                    continue

                inline_title_links = extract_inline_hh_links_from_entities(text, getattr(msg, "entities", None))
                parse_result = parse_spb_vacancies(
                    text,
                    banned_keywords=cfg.telegram_vacancy_banned_words,
                    inline_title_links=inline_title_links,
                )
                detected_vacancies += parse_result.detected_items

                vacancies = parse_result.selected_items
                if not vacancies:
                    continue

                selected_vacancies += len(vacancies)
                matched_posts += 1
                new_hits += 1
                company = vacancies[0].company if vacancies else ""
                header = f"\nКанал: {channel_title} | пост #{msg.id} | {_fmt_dt(msg.date)}"
                if company:
                    header += f" | Компания: {company}"
                lines.append(header)
                for idx, item in enumerate(vacancies, start=1):
                    lines.append(f"{idx}. {item.title}")
                    lines.append(f"   {item.link}")

            set_tg_source_last_id(channel_id, max_seen)
            channel_stats.append(
                ChannelRunStats(
                    channel_ref=channel_ref,
                    channel_title=channel_title,
                    fetched_posts=len(msgs),
                    detected_vacancies=detected_vacancies,
                    selected_vacancies=selected_vacancies,
                )
            )
            logger.info(
                "Channel %s processed: fetched_posts=%s, matched_posts=%s, detected_vacancies=%s, selected_vacancies=%s, last_id=%s",
                channel_ref,
                len(msgs),
                new_hits,
                detected_vacancies,
                selected_vacancies,
                max_seen,
            )

    if matched_posts == 0:
        return "В новых постах по выбранным каналам вакансий СПб не найдено.", 0, channel_stats

    return "\n".join(lines), matched_posts, channel_stats


async def run_house_chats_digest(cfg: Config) -> tuple[str, int, list[HouseChatRunStats]]:
    if not cfg.telegram_user_enabled:
        return "Источник Telegram-чатов отключён (TELEGRAM_USER_ENABLED=0).", 0, []
    if not cfg.telegram_house_chats:
        return "Не настроены домовые чаты (TELEGRAM_HOUSE_CHATS пуст).", 0, []

    llm_client = make_client(cfg.llm_api_key, cfg.llm_base_url)
    tg_client = TelegramClient(StringSession(cfg.telegram_user_session), cfg.telegram_user_api_id, cfg.telegram_user_api_hash)

    lines: list[str] = ["Отчёт по домовым чатам:"]
    total_new_messages = 0
    chat_stats: list[HouseChatRunStats] = []

    async with tg_client:
        if not await tg_client.is_user_authorized():
            raise RuntimeError("Telegram user session is not authorized. Recreate TELEGRAM_USER_SESSION.")

        for house_name, chat_ref in cfg.telegram_house_chats:
            entity = await tg_client.get_entity(chat_ref)
            chat_id = str(entity.id)
            chat_title = getattr(entity, "title", None) or house_name
            last_id = get_tg_house_last_id(chat_id)

            msgs = await tg_client.get_messages(entity, limit=cfg.telegram_source_fetch_limit, min_id=last_id)

            max_seen = last_id
            rendered_messages: list[str] = []

            for msg in reversed(msgs):
                if not msg or not getattr(msg, "id", None):
                    continue
                if msg.id > max_seen:
                    max_seen = msg.id

                text = (msg.message or "").strip()
                if not text:
                    continue

                rendered_messages.append(f"[{_fmt_dt(msg.date)}] {text}")

            set_tg_house_last_id(chat_id, max_seen)
            total_new_messages += len(msgs)
            chat_stats.append(
                HouseChatRunStats(
                    house_name=house_name,
                    chat_ref=chat_ref,
                    chat_title=chat_title,
                    fetched_messages=len(msgs),
                )
            )

            if rendered_messages:
                messages_blob = "\n".join(rendered_messages)
                summary = summarize_house_chat_messages(
                    client=llm_client,
                    model=cfg.llm_model,
                    house_name=house_name,
                    messages_blob=messages_blob,
                    max_output_tokens=cfg.summary_max_output_tokens,
                )
            else:
                summary = "новых обсуждений нет"

            lines.append(f"- {house_name}: {summary}")
            logger.info(
                "House chat %s processed: fetched_messages=%s, last_id=%s",
                chat_ref,
                len(msgs),
                max_seen,
            )

    return "\n".join(lines), total_new_messages, chat_stats
