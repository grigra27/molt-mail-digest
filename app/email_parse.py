from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from email.header import decode_header, make_header
from email.utils import parseaddr
from bs4 import BeautifulSoup
from typing import Optional
import datetime


@dataclass
class ParsedEmail:
    uid: int
    from_name: str
    from_email: str
    subject: str
    date: str
    body_text: str


def _decode_str(s: str | None) -> str:
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s


def _get_best_body(msg: Message) -> str:
    # Prefer text/plain; fallback to text/html stripped to text
    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]

    plain: Optional[str] = None
    html: Optional[str] = None

    for p in parts:
        ctype = p.get_content_type()
        disp = (p.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            continue

        payload = p.get_payload(decode=True)
        if payload is None:
            continue

        charset = p.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except Exception:
            text = payload.decode("utf-8", errors="replace")

        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text

    if plain:
        return plain

    if html:
        soup = BeautifulSoup(html, "lxml")
        # remove scripts/styles
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n")

    return ""


def parse_email(uid: int, raw: bytes) -> ParsedEmail:
    msg = message_from_bytes(raw)

    from_hdr = _decode_str(msg.get("From"))
    name, email_addr = parseaddr(from_hdr)
    name = _decode_str(name).strip()
    email_addr = email_addr.strip()

    subject = _decode_str(msg.get("Subject")).strip()
    date = _decode_str(msg.get("Date")).strip()

    body = _get_best_body(msg)
    return ParsedEmail(
        uid=uid,
        from_name=name,
        from_email=email_addr,
        subject=subject,
        date=date,
        body_text=body.strip(),
    )
