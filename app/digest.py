import logging
from typing import Dict, List, Tuple, Optional
import re

from config import Config
from db import (
    get_last_uid,
    set_last_uid,
    get_uidvalidity,
    set_uidvalidity,
)
from imap_client import ImapClient
from email_parse import parse_email
from cleaner import clean_email_text
from llm import summarize_email, build_digest, make_client

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


def _extract_claim(subject: str) -> Optional[str]:
    subj = subject or ""
    m = CLAIM_RE.search(subj)
    if not m:
        return None
    return m.group(1)


def _build_summary_text(claim_groups: List[Dict], other_items: List[Dict], failed: List[Dict]) -> str:
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

    return "СВОДКА:\n" + "\n".join(lines)


def run_digest(cfg: Config) -> Tuple[str, int, int]:
    """
    Returns: (digest_text, emails_count, failed_count)
    """
    client = make_client(cfg.llm_api_key, cfg.llm_base_url)

    last_uid = get_last_uid()
    old_uidvalidity = get_uidvalidity() or ""

    claim_map: Dict[str, List[Dict]] = {}
    other_items: List[Dict] = []
    failed: List[Dict] = []

    with ImapClient(cfg.imap_host, cfg.imap_port, cfg.imap_user, cfg.imap_password) as im:
        uidvalidity = im.select_folder(cfg.imap_folder)
        if uidvalidity and old_uidvalidity and uidvalidity != old_uidvalidity:
            logger.warning("UIDVALIDITY changed (%s -> %s). Resetting last_uid.", old_uidvalidity, uidvalidity)
            last_uid = 0

        if uidvalidity:
            set_uidvalidity(uidvalidity)

        uids = im.fetch_uids_since(last_uid, cfg.max_emails_per_run)
        if not uids:
            return "СВОДКА:\n- Новых писем нет", 0, 0

        max_uid_processed = last_uid

        for uid in uids:
            raw = im.fetch_rfc822(uid)
            pe = parse_email(uid, raw)

            cleaned = clean_email_text(pe.body_text, cfg.max_chars_per_email)

            from_label = _format_from_label(pe.from_name, pe.from_email)
            subject = pe.subject or ""
            claim_id = _extract_claim(subject)

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
                failed.append({"from_label": from_label, "subject": subject, "reason": str(e)})

            if uid > max_uid_processed:
                max_uid_processed = uid

        # MVP choice: even if LLM fails, we still advance (no reprocessing)
        set_last_uid(max_uid_processed)

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

    summary_text = _build_summary_text(claim_groups, other_items, failed)

    digest_text = build_digest(
        client=client,
        model=cfg.llm_model,
        summary_text=summary_text,
        claim_groups=claim_groups,
        other_items=other_items,
        failed=failed,
        max_output_tokens=cfg.digest_max_output_tokens,
    )

    total = sum(len(g["items"]) for g in claim_groups) + len(other_items) + len(failed)
    return digest_text, total, len(failed)