import re
from dataclasses import dataclass


CITY_HEADER_RE = re.compile(r"^\s*([А-ЯЁA-Z][А-Яа-яЁёA-Za-z\- ]{1,60})\s*:??\s*$")
VACANCY_LINK_RE = re.compile(r"https?://(?:www\.)?hh\.ru/vacancy/\d+", re.IGNORECASE)
COMPANY_RE = re.compile(r"^\s*Компания\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
SPB_ALIASES = (
    "санкт-петербург",
    "санкт петербург",
    "спб",
    "питер",
)
DEFAULT_BANNED_KEYWORDS = ("врач", "водитель", "агент", "терапевт", "диспетчер")


@dataclass(frozen=True)
class VacancyItem:
    title: str
    link: str
    company: str = ""


def _normalize_city(city: str) -> str:
    c = (city or "").strip().lower().replace("ё", "е")
    c = re.sub(r"\s+", " ", c)
    return c


def _normalize_wording(text: str) -> str:
    t = (text or "").strip().lower().replace("ё", "е")
    t = re.sub(r"\s+", " ", t)
    return t


def is_spb_city_header(line: str) -> bool:
    normalized = _normalize_city(line)
    return normalized in SPB_ALIASES


def extract_company_name(text: str) -> str:
    m = COMPANY_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).strip()


def _is_banned_title(title: str, banned_keywords: tuple[str, ...]) -> bool:
    normalized_title = _normalize_wording(title)
    return any(_normalize_wording(w) in normalized_title for w in banned_keywords if w.strip())


def extract_city_block(text: str, target_city: str = "Санкт-Петербург") -> str:
    """
    Extracts a city section from a multi-city vacancy message.

    Returns block including city header and subsequent lines until the next city header.
    Returns empty string if section is absent.
    """
    lines = (text or "").splitlines()
    target = _normalize_city(target_city)

    start = None
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if _normalize_city(line) == target:
            start = idx
            break

    if start is None:
        return ""

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        line = lines[idx].strip()
        if not line:
            continue
        m = CITY_HEADER_RE.match(line)
        if m:
            city_candidate = m.group(1).strip()
            if _normalize_city(city_candidate) != target:
                end = idx
                break

    block = "\n".join(lines[start:end]).strip()
    return block


def extract_spb_vacancies(text: str, banned_keywords: tuple[str, ...] = DEFAULT_BANNED_KEYWORDS) -> list[VacancyItem]:
    """
    Parses Saint Petersburg section into a list of vacancies.

    Expects repeated pattern:
      N. Title — ...
      Ссылка: https://hh.ru/vacancy/123

    Also extracts company from header:
      Компания: <name>

    Banned keywords are applied to title in case-insensitive "contains" mode.
    """
    block = extract_city_block(text, target_city="Санкт-Петербург")
    if not block:
        return []

    company = extract_company_name(text)
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    items: list[VacancyItem] = []

    current_title = ""
    for line in lines[1:]:  # skip city header
        if re.match(r"^\d+\.\s+", line):
            title = re.sub(r"^\d+\.\s+", "", line)
            title = title.split(" — ")[0].strip()
            current_title = title
            continue

        link_m = VACANCY_LINK_RE.search(line)
        if link_m and current_title:
            if not _is_banned_title(current_title, banned_keywords):
                items.append(VacancyItem(title=current_title, link=link_m.group(0), company=company))
            current_title = ""

    return items
