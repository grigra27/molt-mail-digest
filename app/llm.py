from __future__ import annotations

from typing import Dict, List, Any
from openai import OpenAI
import re


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

    # Remove literal "(пусто)" lines and collapse blank lines
    s = re.sub(r"(?mi)^\s*\(пусто\)\s*$", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()

    # If model still outputs labels like "TL;DR:" or "Action:", strip them
    s = re.sub(r"(?mi)^\s*TL;DR:\s*", "", s)
    s = re.sub(r"(?mi)^\s*Action:\s*.*$", "", s)
    s = s.strip()

    # One more collapse after removals
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
- Не используй префиксы "TL;DR:", "Action:" и т.п.
- Не используй markdown (** * # _ `).
- Не добавляй того, чего нет в тексте.

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
        raise RuntimeError(f"Empty TLDR ({_diag(resp)})")

    text = _sanitize_telegram_plain_text(text)

    # Force single line
    text = " ".join(text.split())
    return text.strip()


def build_digest(
    client: OpenAI,
    model: str,
    claim_groups: List[Dict],
    other_items: List[Dict],
    failed: List[Dict],
    max_output_tokens: int,
) -> str:
    """
    Final digest:
    - Claims: already grouped by code (strict, code-generated)
    - Other: grouped by LLM into themes
    - Plain text, no Top Themes, no subjects, no actions.
    """

    # Claims block is deterministic, built in code.
    claims_blocks: List[str] = []
    for g in claim_groups:
        claim_id = g["claim_id"]
        items = g["items"] or []
        lines = []
        for it in items:
            # Format: "- Name (domain): +++ СОДЕРЖАНИЕ: ..."
            lines.append(f"- {it['from_label']}: +++ СОДЕРЖАНИЕ: {it['tldr']}")
        claims_blocks.append(f"[{claim_id}]\n" + "\n".join(lines))

    claims_text = "\n\n".join(claims_blocks) if claims_blocks else "(нет данных)"

    # OTHER items cards for LLM thematic grouping (compact, no subjects in output)
    other_cards: List[str] = []
    for it in other_items:
        other_cards.append(
            f"- From: {it['from_label']}\n"
            f"  Content: {it['tldr']}"
        )

    failed_cards: List[str] = []
    for it in failed:
        failed_cards.append(
            f"- From: {it.get('from_label','unknown')}\n"
            f"  Subject: {(it.get('subject') or '').strip()}\n"
            f"  Reason: {it.get('reason','LLM error')}"
        )

    prompt = f"""
Сформируй Telegram-дайджест в виде ПРОСТОГО ТЕКСТА (PLAIN TEXT).
КРИТИЧНО: НЕ используй markdown и спецсимволы форматирования (** * # _ `).

Нужно:
1) СВОДКА (1–2 строки): сколько заявок и сколько прочих писем, общий смысл.
2) Вставь блок ЗАЯВКИ ниже — НЕ меняй его, НЕ переставляй строки.
3) Для ПРОЧЕЕ (письма без заявок) — сгруппируй по 3–8 темам.
   Внутри темы каждая строка строго:
   - <From>: +++ СОДЕРЖАНИЕ: <Content>
4) Блок НЕ ОБРАБОТАНО показывай только если есть ошибки.

КРИТИЧНО:
- "From" используй РОВНО как дано.
- Не добавляй Subject.
- Не добавляй Action.
- НЕ добавляй раздел "ТОП ТЕМЫ".

Формат итогового текста:

СВОДКА:
- ...

ЗАЯВКИ:
{claims_text}

ПРОЧЕЕ:
[Тема 1]
- From: ... : +++ СОДЕРЖАНИЕ: ...
- ...

[Тема 2]
- ...

НЕ ОБРАБОТАНО:
- From: ... : Subject ...
(только если есть)

Данные для ПРОЧЕЕ:
{chr(10).join(other_cards) if other_cards else "(нет данных)"}

Ошибки:
{chr(10).join(failed_cards) if failed_cards else "(нет)"}
""".strip()

    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_output_tokens,
    )

    text = _extract_output_text(resp)
    if not text:
        raise RuntimeError(f"Empty DIGEST ({_diag(resp)})")

    return _sanitize_telegram_plain_text(text)