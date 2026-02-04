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

    # If model accidentally outputs "ТОП ТЕМЫ" section, strip it (we don't need it)
    # Remove block from "ТОП ТЕМЫ:" until next blank line + next header-ish.
    s = re.sub(r"(?is)\nТОП\s+ТЕМЫ:\n.*?(?=\n[A-ZА-ЯЁ ]{3,}:\n|\nЗАЯВКИ:\n|\nПРОЧЕЕ:\n|\nТЕМЫ:\n|\Z)", "\n", s)
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
Ты — помощник, делающий очень короткий TL;DR для рабочего письма.

КРИТИЧНО:
- Поле "From" используй РОВНО как дано (не сокращай, не убирай домен в скобках).
- Не добавляй email/домен сам — бери только из поля From.
- Не используй markdown (** * # _ `).

Формат ответа:
TL;DR: <1–2 предложения>
Action: <очень коротко>   (только если есть явное действие/ожидание)

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
    return _sanitize_telegram_plain_text(text)


def build_digest(
    client: OpenAI,
    model: str,
    claim_groups: List[Dict],
    other_items: List[Dict],
    failed: List[Dict],
    max_output_tokens: int,
) -> str:
    """
    Build final digest:
    - Claims: already grouped by code (strict)
    - Other: grouped by LLM into themes (since there are many)
    - No "Top themes" block.
    Output is plain text (no markdown).
    """

    # Prepare "claims" part as deterministic plain text with minimal LLM involvement.
    # We will still ask LLM to:
    # - make a short overall summary
    # - group OTHER items into themes
    # But claims listing should be preserved as-is.
    claims_blocks: List[str] = []
    for g in claim_groups:
        claim_id = g["claim_id"]
        items = g["items"] or []
        lines = []
        for it in items:
            # keep compact; subject is optional, but useful sometimes. We'll keep it short.
            subj = it.get("subject", "").strip()
            tldr = it.get("tldr", "").strip()

            # Optional: if subject is very long, truncate to avoid noise
            if len(subj) > 120:
                subj = subj[:120] + "…"

            # One line per email
            if subj:
                lines.append(f"- {it['from_label']}: {tldr} (Subject: {subj})")
            else:
                lines.append(f"- {it['from_label']}: {tldr}")

        claims_blocks.append(f"[{claim_id}]\n" + "\n".join(lines))

    claims_text = "\n\n".join(claims_blocks) if claims_blocks else "(нет)"

    # Prepare OTHER items cards for LLM thematic grouping.
    other_cards: List[str] = []
    for it in other_items:
        subj = (it.get("subject") or "").strip()
        tldr = (it.get("tldr") or "").strip()
        if len(subj) > 160:
            subj = subj[:160] + "…"
        other_cards.append(
            f"- From: {it['from_label']}\n"
            f"  Subject: {subj}\n"
            f"  TLDR: {tldr}"
        )

    failed_cards: List[str] = []
    for it in failed:
        subj = (it.get("subject") or "").strip()
        if len(subj) > 160:
            subj = subj[:160] + "…"
        failed_cards.append(
            f"- From: {it.get('from_label','unknown')}\n"
            f"  Subject: {subj}\n"
            f"  Reason: {it.get('reason','LLM error')}"
        )

    prompt = f"""
Сформируй Telegram-дайджест в виде ПРОСТОГО ТЕКСТА (PLAIN TEXT).
КРИТИЧНО: НЕ используй markdown и спецсимволы форматирования (** * # _ `).

Цели:
1) Сделай короткую СВОДКУ (1–3 строки) по всему объёму.
2) Вставь блок ЗАЯВКИ (он уже подготовлен) — не меняй его структуру, не переставляй строки, не удаляй домены.
3) Для блока ПРОЧЕЕ (письма без заявок) — сгруппируй по 3–8 темам и выведи компактно.
4) Блок НЕ ОБРАБОТАНО показывай только если он не пуст.

КРИТИЧНО:
- Строки "From" используй РОВНО как передано (там уже есть домен в скобках).
- Не удаляй домены.
- Блок "ТОП ТЕМЫ" НЕ НУЖЕН — не добавляй.

Структура итогового текста (строго):

СВОДКА:
- ...

ЗАЯВКИ:
{claims_text}

ПРОЧЕЕ:
[Тема 1]
- <From>: <короткий TL;DR в одну строку>
- ...

[Тема 2]
- ...

НЕ ОБРАБОТАНО:
- From: ... : Subject ...
(только если есть ошибки)

Данные для "ПРОЧЕЕ" (карточки):
{chr(10).join(other_cards) if other_cards else "(нет)"}

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