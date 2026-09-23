import csv
import io
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from config import Config
from db import get_paused, set_paused, get_last_uid, get_stats_history
from digest import run_digest
from telegram_jobs import (
    format_channel_stats,
    format_house_chat_stats,
    run_house_chats_digest,
    run_spb_jobs_digest,
)

logger = logging.getLogger(__name__)


def _is_allowed(update: Update, cfg: Config) -> bool:
    if update.effective_chat is None:
        return False
    return int(update.effective_chat.id) == int(cfg.telegram_chat_id)




def _split_telegram_message(text: str, max_len: int = 3900) -> list[str]:
    """
    Split long text into Telegram-sized chunks without cutting mid-line where possible.
    """
    text = (text or "").strip()
    if len(text) <= max_len:
        return [text] if text else [""]

    lines = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    cur = 0

    for ln in lines:
        add_len = len(ln) + (1 if buf else 0)
        if cur + add_len > max_len and buf:
            chunks.append("\n".join(buf).strip())
            buf = [ln]
            cur = len(ln)
        else:
            if buf:
                cur += 1  # newline
            buf.append(ln)
            cur += len(ln)

    if buf:
        chunks.append("\n".join(buf).strip())

    # Fallback: if a single line is too long, hard-split it
    fixed: list[str] = []
    for ch in chunks:
        if len(ch) <= max_len:
            fixed.append(ch)
        else:
            for i in range(0, len(ch), max_len):
                fixed.append(ch[i:i + max_len])
    return fixed


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not _is_allowed(update, cfg):
        return
    paused = get_paused()
    last_uid = get_last_uid()
    msg = (
        f"Статус:\n"
        f"- paused: {paused}\n"
        f"- last_uid: {last_uid}\n"
        f"- folder: {cfg.imap_folder}\n"
        f"- schedule hours: {cfg.schedule_hours}\n"
        f"- llm: {cfg.llm_model}\n"
        f"- version: 1.2"
    )
    await update.message.reply_text(msg, disable_web_page_preview=True)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not _is_allowed(update, cfg):
        return
    set_paused(True)
    await update.message.reply_text(
        "Ок, поставил на паузу. Авто-дайджесты не будут отправляться.",
        disable_web_page_preview=True,
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not _is_allowed(update, cfg):
        return
    set_paused(False)
    await update.message.reply_text(
        "Ок, снял с паузы. Авто-дайджесты снова будут отправляться.",
        disable_web_page_preview=True,
    )


async def cmd_digest_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not _is_allowed(update, cfg):
        return

    await update.message.reply_text("Делаю дайджест…", disable_web_page_preview=True)
    try:
        text, total, failed = run_digest(cfg)
        for chunk in _split_telegram_message(text):
            await update.message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True)

        await update.message.reply_text(
            f"Готово. Писем: {total}, не обработано: {failed}.",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.exception("digest_now failed")
        await update.message.reply_text(
            f"Ошибка при формировании дайджеста: {e}",
            disable_web_page_preview=True,
        )


async def cmd_jobs_spb_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not _is_allowed(update, cfg):
        return

    await update.message.reply_text("Ищу вакансии СПб в Telegram-каналах…", disable_web_page_preview=True)
    try:
        text, matched_posts, _channel_stats = await run_spb_jobs_digest(cfg)
        for chunk in _split_telegram_message(text):
            await update.message.reply_text(chunk, disable_web_page_preview=True)

        await update.message.reply_text(
            f"Готово. Подходящих постов: {matched_posts}.",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.exception("jobs_spb_now failed")
        await update.message.reply_text(
            f"Ошибка при сборе вакансий СПб: {e}",
            disable_web_page_preview=True,
        )


async def cmd_house_chats_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not _is_allowed(update, cfg):
        return

    await update.message.reply_text("Собираю обновления из домовых чатов…", disable_web_page_preview=True)
    try:
        text, total_messages, chat_stats = await run_house_chats_digest(cfg)
        for chunk in _split_telegram_message(text):
            await update.message.reply_text(chunk, disable_web_page_preview=True)

        await update.message.reply_text(
            f"Готово. Новых сообщений: {total_messages}.\n{format_house_chat_stats(chat_stats)}",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.exception("house_chats_now failed")
        await update.message.reply_text(
            f"Ошибка при сборе домовых чатов: {e}",
            disable_web_page_preview=True,
        )


async def cmd_stats_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.bot_data["cfg"]
    if not _is_allowed(update, cfg):
        return

    history = get_stats_history()
    if not history:
        await update.message.reply_text(
            "Истории статистики пока нет — данные появляются после дайджестов.",
            disable_web_page_preview=True,
        )
        return

    # BOM so Excel opens Cyrillic correctly
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["date", "total", "other", "claims_total", "claims_json"])
    for row in history:
        claims_total = sum(row["claims"].values())
        writer.writerow([
            row["date"],
            row["total"],
            row["other"],
            claims_total,
            json.dumps(row["claims"], ensure_ascii=False),
        ])

    payload = ("\ufeff" + buf.getvalue()).encode("utf-8")
    filename = f"stats_daily_{datetime.now(ZoneInfo(cfg.tz)).strftime('%Y%m%d')}.csv"

    await update.message.reply_document(
        document=io.BytesIO(payload),
        filename=filename,
        caption=f"Ежедневная статистика писем: {len(history)} дн., всего {sum(r['total'] for r in history)}",
    )


async def send_to_owner(app: Application, cfg: Config, text: str, parse_mode: str | None = None) -> None:
    for chunk in _split_telegram_message(text):
        await app.bot.send_message(
            chat_id=cfg.telegram_chat_id,
            text=chunk,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )


BOT_COMMANDS = [
    BotCommand("status", "Текущее состояние дайджеста"),
    BotCommand("digest_now", "Собрать и отправить дайджест сейчас"),
    BotCommand("house_chats_now", "Сводка из домовых чатов"),
    BotCommand("jobs_spb_now", "Вакансии СПб из Telegram-каналов"),
    BotCommand("stats_csv", "Выгрузить ежедневную статистику писем (CSV)"),
    BotCommand("pause", "Пауза авто-дайджестов"),
    BotCommand("resume", "Снять паузу"),
]


async def register_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)


def build_app(cfg: Config) -> Application:
    application = Application.builder().token(cfg.telegram_bot_token).build()
    application.bot_data["cfg"] = cfg

    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("digest_now", cmd_digest_now))
    application.add_handler(CommandHandler("jobs_spb_now", cmd_jobs_spb_now))
    application.add_handler(CommandHandler("house_chats_now", cmd_house_chats_now))
    application.add_handler(CommandHandler("stats_csv", cmd_stats_csv))

    return application
