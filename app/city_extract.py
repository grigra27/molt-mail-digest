import re
from dataclasses import dataclass


CITY_HEADER_RE = re.compile(r"^\s*([А-ЯЁA-Z][А-Яа-яЁёA-Za-z\- ]{1,60})\s*:?\s*$")
CITY_HEADER_WITH_COUNT_RE = re.compile(r"^\s*([А-ЯЁA-Z][А-Яа-яЁёA-Za-z\- ]{1,60})\s*\(\d+\)\s*:?\s*$")
VACANCY_LINK_RE = re.compile(r"https?://(?:www\.)?hh\.ru/vacancy/\d+", re.IGNORECASE)
COMPANY_RE = re.compile(r"^\s*Компания\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
SPB_ALIASES = (
    "санкт-петербург",
    "санкт петербург",
    "спб",
    "питер",
)
DEFAULT_BANNED_KEYWORDS = ("врач", "водитель", "агент", "терапевт", "диспетчер")
REMOTE_WORK_KEYWORDS = ("удаленная работа", "удалённая работа")


@dataclass(frozen=True)
class VacancyItem:
    title: str
    link: str
    company: str = ""


@dataclass(frozen=True)
class VacancyParseResult:
    selected_items: list[VacancyItem]
    detected_items: int


def _normalize_city(city: str) -> str:
    c = (city or "").strip().lower().replace("ё", "е")
    c = re.sub(r"\s+", " ", c)
    return c


def _normalize_wording(text: str) -> str:
    t = (text or "").strip().lower().replace("ё", "е")
    t = re.sub(r"\s+", " ", t)
    return t


def _extract_city_from_header(line: str) -> str:
    raw = (line or "").strip()
    m_count = CITY_HEADER_WITH_COUNT_RE.match(raw)
    if m_count:
        return _normalize_city(m_count.group(1))

    m = CITY_HEADER_RE.match(raw)
    if m:
        return _normalize_city(m.group(1))
    return _normalize_city(raw.rstrip(":"))


def _is_city_header_line(line: str) -> bool:
    raw = (line or "").strip()
    return bool(CITY_HEADER_WITH_COUNT_RE.match(raw) or CITY_HEADER_RE.match(raw))


def extract_inline_hh_links_from_entities(text: str, entities: list[object] | None) -> dict[str, str]:
    """Extract hidden HH links from Telegram entities keyed by normalized visible text."""
    if not text or not entities:
        return {}

    utf16_unit_to_index: dict[int, int] = {}
    utf16_pos = 0
    for idx, ch in enumerate(text):
        utf16_unit_to_index[utf16_pos] = idx
        utf16_pos += 2 if ord(ch) > 0xFFFF else 1
    utf16_unit_to_index[utf16_pos] = len(text)

    def _utf16_slice(source: str, offset_utf16: int, length_utf16: int) -> str:
        start = utf16_unit_to_index.get(offset_utf16)
        end = utf16_unit_to_index.get(offset_utf16 + length_utf16)
        if start is None or end is None:
            return ""
        return source[start:end]

    links_by_title: dict[str, str] = {}
    for entity in entities:
        url = getattr(entity, "url", "") or ""
        if not VACANCY_LINK_RE.search(url):
            continue

        offset = int(getattr(entity, "offset", -1))
        length = int(getattr(entity, "length", 0))
        if offset < 0 or length <= 0:
            continue

        visible_text = _utf16_slice(text, offset, length).strip()
        if not visible_text:
            continue

        links_by_title[_normalize_wording(visible_text)] = url

    return links_by_title


def is_spb_city_header(line: str) -> bool:
    normalized = _extract_city_from_header(line)
    return normalized in SPB_ALIASES


def extract_company_name(text: str) -> str:
    m = COMPANY_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).strip()


def _is_banned_title(title: str, banned_keywords: tuple[str, ...]) -> bool:
    normalized_title = _normalize_wording(title)
    return any(_normalize_wording(w) in normalized_title for w in banned_keywords if w.strip())


def _is_remote_work_line(text: str) -> bool:
    normalized = _normalize_wording(text)
    return any(k in normalized for k in REMOTE_WORK_KEYWORDS)


def extract_city_block(text: str, target_city: str = "Санкт-Петербург") -> str:
    """
    Extracts a city section from a multi-city vacancy message.

    Returns block including city header and subsequent lines until the next city header.
    Returns empty string if section is absent.
    """
    lines = (text or "").splitlines()
    target = _normalize_city(target_city)
    target_aliases = {target}
    if target in SPB_ALIASES:
        target_aliases.update(SPB_ALIASES)

    start = None
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if _is_city_header_line(line) and _extract_city_from_header(line) in target_aliases:
            start = idx
            break

    if start is None:
        return ""

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        line = lines[idx].strip()
        if not line:
            continue
        if _is_city_header_line(line):
            city_candidate = _extract_city_from_header(line)
            if city_candidate not in target_aliases:
                end = idx
                break

    block = "\n".join(lines[start:end]).strip()
    return block


def parse_spb_vacancies(
    text: str,
    banned_keywords: tuple[str, ...] = DEFAULT_BANNED_KEYWORDS,
    inline_title_links: dict[str, str] | None = None,
) -> VacancyParseResult:
    """Parse SPB vacancies and return both detected and selected counts."""
    block = extract_city_block(text, target_city="Санкт-Петербург")
    if not block:
        return VacancyParseResult(selected_items=[], detected_items=0)

    company = extract_company_name(text)
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    selected_items: list[VacancyItem] = []
    detected_items = 0
    inline_title_links = inline_title_links or {}

    current_title = ""
    current_is_remote = False
    for line in lines[1:]:  # skip city header
        if re.match(r"^\d+\.\s+", line):
            raw_title = re.sub(r"^\d+\.\s+", "", line)
            current_is_remote = _is_remote_work_line(raw_title)
            title = raw_title.split(" — ")[0].strip()
            inline_link = inline_title_links.get(_normalize_wording(title))
            if inline_link:
                if not current_is_remote:
                    detected_items += 1
                if not current_is_remote and not _is_banned_title(title, banned_keywords):
                    selected_items.append(VacancyItem(title=title, link=inline_link, company=company))
                current_title = ""
                current_is_remote = False
                continue
            current_title = title
            continue

        link_m = VACANCY_LINK_RE.search(line)
        if link_m and current_title:
            if not current_is_remote:
                detected_items += 1
            if not current_is_remote and not _is_banned_title(current_title, banned_keywords):
                selected_items.append(VacancyItem(title=current_title, link=link_m.group(0), company=company))
            current_title = ""
            current_is_remote = False

    return VacancyParseResult(selected_items=selected_items, detected_items=detected_items)


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
    return parse_spb_vacancies(text, banned_keywords=banned_keywords).selected_items


def parse_remote_vacancies(
    text: str,
    banned_keywords: tuple[str, ...] = DEFAULT_BANNED_KEYWORDS,
    inline_title_links: dict[str, str] | None = None,
) -> VacancyParseResult:
    """Parse vacancies marked as remote work regardless of city section."""
    if not text:
        return VacancyParseResult(selected_items=[], detected_items=0)

    inline_title_links = inline_title_links or {}
    company = extract_company_name(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    selected_items: list[VacancyItem] = []
    detected_items = 0
    current_title = ""
    current_is_remote = False

    for line in lines:
        if re.match(r"^\d+\.\s+", line):
            raw_title = re.sub(r"^\d+\.\s+", "", line)
            title = raw_title.split(" — ")[0].strip()
            current_is_remote = _is_remote_work_line(raw_title)

            inline_link = inline_title_links.get(_normalize_wording(title))
            if inline_link and current_is_remote:
                detected_items += 1
                if not _is_banned_title(title, banned_keywords):
                    selected_items.append(VacancyItem(title=title, link=inline_link, company=company))
                current_title = ""
                current_is_remote = False
                continue

            current_title = title
            continue

        link_m = VACANCY_LINK_RE.search(line)
        if link_m and current_title and current_is_remote:
            detected_items += 1
            if not _is_banned_title(current_title, banned_keywords):
                selected_items.append(VacancyItem(title=current_title, link=link_m.group(0), company=company))
            current_title = ""
            current_is_remote = False

    return VacancyParseResult(selected_items=selected_items, detected_items=detected_items)
