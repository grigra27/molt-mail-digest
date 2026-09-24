import html
import logging
from typing import Callable, Dict, List, Tuple, Optional
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Config
from db import (
    get_last_uid,
    set_last_uid,
    get_uidvalidity,
    set_uidvalidity,
    add_daily_stats,
    get_today_daily_stats,
)
from imap_client import ImapClient
from email_parse import parse_email
from cleaner import clean_email_text
from llm import summarize_email, group_other_items, make_client

logger = logging.getLogger(__name__)

# Exactly 5 digits (not part of longer number), optionally followed by up to 3 "-WORD" chunks
# WORD can be Cyrillic/Latin letters, 1..4 chars each (fits ЛТ, МСК, АР, КЗ, etc.)
CLAIM_RE = re.compile(r"(?<!\d)(\d{5}(?:-[A-Za-zА-Яа-яЁё]{1,4}){0,3})(?!\d)")


def _email_domain(addr: str) -> str:
    addr = (addr or "").strip()
    if "@" in addr:
        return addr.split("@", 1)[1].lower().strip()
    return ""


def _format_from_label(name: str, email_addr: str) -> str:
    name = (name or "").strip()
    dom = _email_domain(email_addr)
    if name and dom:
        return f"{name} ({dom})"
    if name:
        return name
    if dom:
        return dom
    return email_addr or "unknown"


def _format_from_html(name: str, domain: str) -> str:
    name = (name or "").strip()
    domain = (domain or "").strip()
    if name and domain:
        return f"<b>{html.escape(name)}</b> <i>({html.escape(domain)})</i>"
    if name:
        return f"<b>{html.escape(name)}</b>"
    if domain:
        return f"<b>{html.escape(domain)}</b>"
    return "<b>unknown</b>"


def _render_item_line(item: Dict) -> str:
    who = _format_from_html(item.get("from_name", ""), item.get("from_domain", ""))
    return f"• {who} — {html.escape(item['content'])}"


def _extract_claim(subject: str) -> Optional[str]:
    subj = subject or ""
    m = CLAIM_RE.search(subj)
    if not m:
        return None
    return m.group(1)


def _build_summary_lines(
    claim_groups: List[Dict], other_items: List[Dict], failed: List[Dict], pending: int
) -> List[str]:
    claims_count = len(claim_groups)
    claim_emails_count = sum(len(g["items"]) for g in claim_groups)
    other_count = len(other_items)
    failed_count = len(failed)

    lines: List[str] = []
    if claims_count or other_count or failed_count:
        if claims_count:
            lines.append(f"- Заявок: {claims_count} (писем по заявкам: {claim_emails_count})")
        if other_count:
            lines.append(f"- Прочих писем: {other_count}")
        if failed_count:
            lines.append(f"- Не обработано: {failed_count}")
    else:
        lines.append("- Новых писем нет")

    if pending:
        lines.append(f"- Ещё в очереди: {pending} (попадут в следующий дайджест)")

    return lines


def _render_digest(
    claim_groups: List[Dict],
    other_items: List[Dict],
    other_groups: List[Dict],
    failed: List[Dict],
    pending: int = 0,
) -> str:
    sections: List[str] = []

    summary_lines = _build_summary_lines(claim_groups, other_items, failed, pending)
    sections.append("<b>СВОДКА</b>\n" + "\n".join(summary_lines))

    if claim_groups:
        blocks = []
        for g in claim_groups:
            header = f"<b>{html.escape(g['claim_id'])}</b>"
            lines = [_render_item_line(it) for it in g["items"]]
            blocks.append(header + "\n" + "\n".join(lines))
        sections.append("<b>ЗАЯВКИ</b>\n\n" + "\n\n".join(blocks))

    if other_groups:
        blocks = []
        for grp in other_groups:
            header = f"<b>{html.escape(grp['theme'])}</b>"
            lines = [_render_item_line(other_items[i]) for i in grp["items"]]
            blocks.append(header + "\n" + "\n".join(lines))
        sections.append("<b>ПРОЧЕЕ</b>\n\n" + "\n\n".join(blocks))

    if failed:
        lines = []
        for it in failed:
            who = _format_from_html(it.get("from_name", ""), it.get("from_domain", ""))
            subj = html.escape(it.get("subject") or "")
            reason = html.escape(it.get("reason") or "")
            lines.append(f"• {who} — тема: {subj} · ошибка: {reason}")
        sections.append("<b>НЕ ОБРАБОТАНО</b>\n" + "\n".join(lines))

    return "\n\n".join(sections)


def run_digest(cfg: Config) -> Tuple[str, int, int, Callable[[], None]]:
    """
    Returns: (digest_text, emails_count, failed_count, commit)

    Nothing is persisted here: the caller must invoke commit() only after
    the digest has actually been delivered, otherwise a failed send would
    mark the emails as processed and they would never be shown.
    """
    client = make_client(cfg.llm_api_key, cfg.llm_base_url)

    last_uid = get_last_uid()
    old_uidvalidity = get_uidvalidity() or ""

    claim_map: Dict[str, List[Dict]] = {}
    other_items: List[Dict] = []
    failed: List[Dict] = []
    claim_deltas: Dict[str, int] = {}
    other_delta = 0

    with ImapClient(cfg.imap_host, cfg.imap_port, cfg.imap_user, cfg.imap_password) as im:
        uidvalidity = im.select_folder(cfg.imap_folder)
        if uidvalidity and old_uidvalidity and uidvalidity != old_uidvalidity:
            logger.warning("UIDVALIDITY changed (%s -> %s). Resetting last_uid.", old_uidvalidity, uidvalidity)
            last_uid = 0

        def commit_state(new_last_uid: int) -> None:
            if uidvalidity:
                set_uidvalidity(uidvalidity)
            set_last_uid(new_last_uid)

        all_uids = im.fetch_uids_since(last_uid)
        # Oldest first: the rest stays above last_uid and is picked up next run
        # (taking the newest would advance last_uid past the skipped ones forever).
        uids = all_uids[:cfg.max_emails_per_run]
        pending = len(all_uids) - len(uids)
        if pending:
            logger.info("Backlog: processing %s emails, %s left for next run", len(uids), pending)
        if not uids:
            return "<b>СВОДКА</b>\n- Новых писем нет", 0, 0, lambda: commit_state(last_uid)

        max_uid_processed = last_uid

        for uid in uids:
            raw = im.fetch_rfc822(uid)
            pe = parse_email(uid, raw)

            cleaned = clean_email_text(pe.body_text, cfg.max_chars_per_email)

            from_name = (pe.from_name or "").strip()
            from_domain = _email_domain(pe.from_email)
            from_label = _format_from_label(pe.from_name, pe.from_email)
            subject = pe.subject or ""
            claim_id = _extract_claim(subject)
            if claim_id:
                claim_deltas[claim_id] = claim_deltas.get(claim_id, 0) + 1
            else:
                other_delta += 1

            try:
                content_line = summarize_email(
                    client=client,
                    model=cfg.llm_model,
                    subject=subject,
                    from_label=from_label,
                    body=cleaned,
                    max_output_tokens=cfg.summary_max_output_tokens,
                )

                item = {
                    "uid": uid,
                    "from_name": from_name,
                    "from_domain": from_domain,
                    "from_label": from_label,
                    "subject": subject,  # keep only for failed/debug
                    "content": content_line,  # one-line content, no TL;DR/Action/Subject
                    "claim_id": claim_id,
                }

                if claim_id:
                    claim_map.setdefault(claim_id, []).append(item)
                else:
                    other_items.append(item)

            except Exception as e:
                logger.exception("LLM summarize failed for UID=%s", uid)
                reason = str(e)
                if len(reason) > 300:
                    reason = reason[:300] + "…"
                failed.append({
                    "from_name": from_name,
                    "from_domain": from_domain,
                    "from_label": from_label,
                    "subject": subject,
                    "reason": reason,
                })

            if uid > max_uid_processed:
                max_uid_processed = uid

    # MVP choice: even if LLM fails for an email, we still advance (no reprocessing)
    def commit() -> None:
        commit_state(max_uid_processed)
        add_daily_stats(
            timezone=cfg.tz,
            total_delta=len(uids),
            other_delta=other_delta,
            claim_deltas=claim_deltas,
        )

    # Build claim groups sorted:
    claim_groups: List[Dict] = []
    for claim_id, items in claim_map.items():
        items_sorted = sorted(items, key=lambda x: x["uid"])
        last_uid_in_claim = items_sorted[-1]["uid"] if items_sorted else 0
        claim_groups.append({"claim_id": claim_id, "items": items_sorted, "last_uid": last_uid_in_claim})

    # Most recent claim groups first
    claim_groups.sort(key=lambda g: g["last_uid"], reverse=True)
    # Other items in chronological order
    other_items = sorted(other_items, key=lambda x: x["uid"])

    other_groups = group_other_items(
        client=client,
        model=cfg.llm_model,
        other_items=other_items,
        max_output_tokens=cfg.digest_max_output_tokens,
    )

    digest_text = _render_digest(claim_groups, other_items, other_groups, failed, pending)

    total = sum(len(g["items"]) for g in claim_groups) + len(other_items) + len(failed)
    return digest_text, total, len(failed), commit


def build_daily_stats_text(cfg: Config) -> str:
    stats = get_today_daily_stats(cfg.tz)
    date_local = datetime.now(ZoneInfo(cfg.tz)).strftime("%d.%m.%Y")

    lines: List[str] = [
        f"СТАТИСТИКА ЗА ДЕНЬ ({date_local}):",
        f"- Всего писем: {stats['total']}",
    ]

    claims: Dict[str, int] = stats.get("claims", {})
    if claims:
        lines.append("- По заявкам:")
        for claim_id in sorted(claims.keys()):
            lines.append(f"  • {claim_id}: {claims[claim_id]}")
    else:
        lines.append("- По заявкам: 0")

    lines.append(f"- Прочие: {stats['other']}")
    return "\n".join(lines)
