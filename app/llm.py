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


def build_digest(
    client: OpenAI,
    model: str,
    summary_text: str,
    claim_groups: List[Dict],
    other_items: List[Dict],
    failed: List[Dict],
    max_output_tokens: int,
) -> str:
    """
    Final digest:
    - summary_text is computed by code (NO model-made counts)
    - claims are listed deterministically by code
    - OTHER is grouped by LLM into themes
    - No Subject, no Action, no Top Themes
    - Plain text
    """

    # Deterministic claims listing
    claim_blocks: List[str] = []
    for g in claim_groups:
        claim_id = g["claim_id"]
        lines: List[str] = []
        for it in g["items"]:
            # Required output line:
            # - Name (domain): +++ СОДЕРЖАНИЕ: ...
            lines.append(f"- {it['from_label']}: +++ СОДЕРЖАНИЕ: {it['content']}")
        claim_blocks.append(f"[{claim_id}]\n" + "\n".join(lines))

    claims_text = "\n\n".join(claim_blocks) if claim_blocks else "(нет данных)"

    # Provide OTHER cards to LLM for thematic grouping
    other_cards: List[str] = []
    for it in other_items:
        other_cards.append(f"- From: {it['from_label']}\n  Content: {it['content']}")

    failed_cards: List[str] = []
    for it in failed:
        subj = (it.get("subject") or "").strip()
        if len(subj) > 180:
            subj = subj[:180] + "…"
        failed_cards.append(
            f"- From: {it.get('from_label','unknown')}\n"
            f"  Subject: {subj}\n"
            f"  Reason: {it.get('reason','LLM error')}"
        )

    prompt = f"""
Сформируй Telegram-дайджест в виде ПРОСТОГО ТЕКСТА (PLAIN TEXT).
КРИТИЧНО: НЕ используй markdown и спецсимволы форматирования (** * # _ `).

КРИТИЧНО:
- Блок "СВОДКА" уже посчитан кодом. Вставь его РОВНО как есть. Не меняй цифры и формулировки.
- Блок "ЗАЯВКИ" уже подготовлен кодом. Вставь его РОВНО как есть. Не меняй и не переставляй строки.
- Не добавляй Subject.
- Не добавляй Action.
- Не добавляй "ТОП ТЕМЫ".

Нужно:
1) Вставить готовую СВОДКУ.
2) Вставить готовые ЗАЯВКИ.
3) Сформировать блок ПРОЧЕЕ: сгруппировать письма без заявок по 3–8 темам.
   Внутри темы каждая строка строго:
   - <From>: +++ СОДЕРЖАНИЕ: <Content>
4) Блок НЕ ОБРАБОТАНО показывать только если есть ошибки.

Формат итогового текста (строго):

{summary_text}

ЗАЯВКИ:
{claims_text}

ПРОЧЕЕ:
[Тема 1]
- <From>: +++ СОДЕРЖАНИЕ: <Content>
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
        raise RuntimeError(f"Empty digest ({_diag(resp)})")

    return _sanitize_telegram_plain_text(text)