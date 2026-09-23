from __future__ import annotations

from typing import Dict, List, Any
from openai import OpenAI
import json
import logging
import re

logger = logging.getLogger(__name__)


def make_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


def _extract_output_text(resp: Any) -> str:
    txt = getattr(resp, "output_text", None)
    if isinstance(txt, str) and txt.strip():
        return txt.strip()

    out = getattr(resp, "output", None) or []
    parts: List[str] = []

    for item in out:
        if getattr(item, "type", None) == "message":
            content = getattr(item, "content", None) or []
            for c in content:
                if getattr(c, "type", None) == "output_text":
                    t = getattr(c, "text", "")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
                maybe_text = getattr(c, "value", None)
                if isinstance(maybe_text, str) and maybe_text.strip():
                    parts.append(maybe_text.strip())

    return "\n".join(parts).strip()


def _diag(resp: Any) -> str:
    status = getattr(resp, "status", None)
    incomplete_details = getattr(resp, "incomplete_details", None)
    error = getattr(resp, "error", None)

    pieces = []
    if status:
        pieces.append(f"status={status}")
    if incomplete_details:
        pieces.append(f"incomplete_details={incomplete_details}")
    if error:
        pieces.append(f"error={error}")
    return ", ".join(pieces) if pieces else "no_diagnostics"


def _sanitize_telegram_plain_text(s: str) -> str:
    if not s:
        return s

    s = s.replace("\r\n", "\n").replace("\r", "\n")

    # Remove markdown remnants if model emits them
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", s)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"(?m)^\s*\*\s+", "- ", s)
    s = s.replace("```", "")

    # Remove "(пусто)" lines and collapse blanks
    s = re.sub(r"(?mi)^\s*\(пусто\)\s*$", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()

    # Strip accidental TL;DR / Action prefixes if they appear
    s = re.sub(r"(?mi)^\s*TL;DR:\s*", "", s)
    s = re.sub(r"(?mi)^\s*Action:\s*.*$", "", s)
    s = s.strip()

    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def summarize_email(
    client: OpenAI,
    model: str,
    subject: str,
    from_label: str,
    body: str,
    max_output_tokens: int,
) -> str:
    prompt = f"""
Сделай очень короткое содержание рабочего письма.

КРИТИЧНО:
- Верни только ОДНУ строку (без переносов), 6–20 слов, по смыслу.
- Никаких префиксов: не пиши "TL;DR:", "Action:" и т.п.
- Не используй markdown (** * # _ `).
- Не упоминай тему письма (Subject) и не пересказывай её буквально.
- Не выдумывай факты.

Данные:
From: {from_label}
Subject: {subject}

Текст письма:
{body}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_output_tokens,
    )

    text = _extract_output_text(resp)
    if not text:
        raise RuntimeError(f"Empty content ({_diag(resp)})")

    text = _sanitize_telegram_plain_text(text)

    # Force single line (Telegram-friendly)
    text = " ".join(text.split())
    return text.strip()


def _parse_theme_groups(text: str, n_items: int) -> List[Dict[str, Any]]:
    """
    Parses LLM output into [{"theme": str, "items": [idx, ...]}, ...].
    Falls back to a single "Разное" group covering any item the LLM
    didn't place (or all items, if parsing fails outright) — no email
    is ever silently dropped from the digest.
    """
    groups: List[Dict[str, Any]] = []
    seen: set[int] = set()

    try:
        cleaned = re.sub(r"```(?:json)?", "", text).strip()
        m = re.search(r"\[.*\]", cleaned, re.S)
        raw = json.loads(m.group(0) if m else cleaned)
        for g in raw:
            theme = str(g.get("theme", "")).strip() or "Разное"
            idxs: List[int] = []
            for i in g.get("items", []):
                try:
                    i = int(i)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < n_items and i not in seen:
                    idxs.append(i)
                    seen.add(i)
            if idxs:
                groups.append({"theme": theme, "items": idxs})
    except Exception:
        logger.exception("Failed to parse theme groups from LLM output")
        groups = []
        seen = set()

    leftover = [i for i in range(n_items) if i not in seen]
    if leftover:
        groups.append({"theme": "Разное", "items": leftover})

    return groups


def group_other_items(
    client: OpenAI,
    model: str,
    other_items: List[Dict],
    max_output_tokens: int,
) -> List[Dict[str, Any]]:
    """
    Groups non-claim emails into 3-8 themes. Formatting of the digest
    itself is handled deterministically by the caller — this only
    decides which theme each email belongs to.
    """
    if not other_items:
        return []

    cards = [f"{idx}: {it['from_label']}: {it['content']}" for idx, it in enumerate(other_items)]

    prompt = f"""
Сгруппируй письма по темам (3–8 тематических групп) для дайджеста.

КРИТИЧНО:
- Верни ТОЛЬКО JSON-массив, без markdown и пояснений.
- Формат: [{{"theme": "Название темы", "items": [0, 2, 5]}}, ...]
- "items" — индексы писем из списка ниже (0-based), каждый индекс должен встретиться ровно один раз.
- Названия тем короткие (2–5 слов), по-русски, без нумерации.
- Не выдумывай новые письма и не меняй индексы.

Письма:
{chr(10).join(cards)}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_output_tokens,
    )

    text = _extract_output_text(resp)
    return _parse_theme_groups(text, len(other_items))


def summarize_house_chat_messages(
    client: OpenAI,
    model: str,
    house_name: str,
    messages_blob: str,
    max_output_tokens: int,
) -> str:
    prompt = f"""
Сделай короткую сводку обсуждений в домовом чате.

КРИТИЧНО:
- Верни только ОДНУ строку на русском языке, 8-30 слов.
- Без markdown.
- Только факты из сообщений, без выдумок.
- Если сообщений нет или они пустые, верни: новых обсуждений нет.

Дом: {house_name}

Сообщения:
{messages_blob}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_output_tokens,
    )

    text = _extract_output_text(resp)
    if not text:
        raise RuntimeError(f"Empty house chat summary ({_diag(resp)})")

    text = _sanitize_telegram_plain_text(text)
    return " ".join(text.split()).strip()
