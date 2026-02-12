import re

# Начало процитированного блока (Outlook англ/рус)
_QUOTED_BLOCK_START = re.compile(
    r"^(From|От):\s+.+$|^-{2,}\s*Original Message\s*-{2,}$",
    re.IGNORECASE
)

# Заголовки внутри quoted-блоков
_HEADER_LINES = re.compile(
    r"^(From|От|Sent|Дата|To|Кому|Cc|Копия|Subject|Тема|Importance|Важность):\s+.*$",
    re.IGNORECASE
)

_EXTERNAL_BANNER = re.compile(
    r"^Внимание:\s*письмо от внешнего отправителя\.",
    re.IGNORECASE
)

_SIGNATURE_MARKERS = [
    re.compile(r"^--\s*$"),
    re.compile(r"^С уважением\b.*$", re.IGNORECASE),
    re.compile(r"^Best regards\b.*$", re.IGNORECASE),
]

def _strip_noise_lines(lines: list[str]) -> list[str]:
    out = []
    skip_banner = False

    for ln in lines:
        s = ln.strip()

        if _EXTERNAL_BANNER.match(s):
            skip_banner = True
            continue

        if skip_banner:
            if s == "":
                skip_banner = False
            continue

        out.append(ln.rstrip())

    return out


def clean_email_text_middle(text: str, max_chars: int, keep_quoted_blocks: int = 2) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in t.split("\n")]
    lines = _strip_noise_lines(lines)

    # Разбиваем на блоки
    blocks: list[list[str]] = [[]]

    for ln in lines:
        s = ln.strip()
        if _QUOTED_BLOCK_START.match(s):
            blocks.append([ln])
        else:
            blocks[-1].append(ln)

    # Обрезаем подпись только в верхнем блоке
    top: list[str] = []
    for ln in blocks[0]:
        s = ln.strip()
        if any(p.match(s) for p in _SIGNATURE_MARKERS):
            break
        top.append(ln)
    blocks[0] = top

    picked = blocks[:1 + max(0, keep_quoted_blocks)]

    cleaned_blocks: list[str] = []

    for i, b in enumerate(picked):
        if i == 0:
            chunk = "\n".join(b).strip()
        else:
            filtered = []
            for ln in b:
                s = ln.strip()
                if _HEADER_LINES.match(s):
                    continue
                filtered.append(ln)
            chunk = "\n".join(filtered).strip()

        if chunk:
            cleaned_blocks.append(chunk)

    out = "\n\n---\n\n".join(cleaned_blocks).strip()

    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[TRUNCATED]"

    return out


# Сохраняем прежний интерфейс
def clean_email_text(text: str, max_chars: int) -> str:
    return clean_email_text_middle(text, max_chars, keep_quoted_blocks=2)