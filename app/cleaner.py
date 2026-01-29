import re


_QUOTE_MARKERS = [
    re.compile(r"^>+"),
    re.compile(r"^On .+ wrote:$", re.IGNORECASE),
    re.compile(r"^От: .+$", re.IGNORECASE),
    re.compile(r"^Sent: .+$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
]

_SIGNATURE_MARKERS = [
    re.compile(r"^--\s*$"),
    re.compile(r"^С уважением[,!]*\s*$", re.IGNORECASE),
    re.compile(r"^Best regards[,!]*\s*$", re.IGNORECASE),
]


def clean_email_text(text: str, max_chars: int) -> str:
    # Normalize newlines
    t = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = [ln.rstrip() for ln in t.split("\n")]

    cleaned = []
    for ln in lines:
        # drop very long whitespace-only blocks
        if ln.strip() == "":
            cleaned.append("")
            continue

        # remove quoted lines
        if any(p.match(ln.strip()) for p in _QUOTE_MARKERS):
            continue

        cleaned.append(ln)

    # Cut at signature markers (first occurrence)
    final_lines = []
    for ln in cleaned:
        if any(p.match(ln.strip()) for p in _SIGNATURE_MARKERS):
            break
        final_lines.append(ln)

    out = "\n".join(final_lines).strip()

    # Hard truncate
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[TRUNCATED]"
    return out
