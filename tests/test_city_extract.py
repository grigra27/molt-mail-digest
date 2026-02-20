import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "app"))

import unittest

from city_extract import (
    extract_city_block,
    extract_company_name,
    extract_inline_hh_links_from_entities,
    extract_spb_vacancies,
    parse_remote_vacancies,
    parse_spb_vacancies,
)


SAMPLE = """Компания: Абсолют Страхование

Москва
1. Java-разработчик — ЗП не указана
Ссылка: https://hh.ru/vacancy/130121074

Санкт-Петербург
1. Главный специалист управления ипотечного страхования — ЗП не указана
Ссылка: https://hh.ru/vacancy/130176260

2. Ведущий специалист — ЗП не указана
Ссылка: https://hh.ru/vacancy/129959488

3. Начальник управления страхования грузов — ЗП не указана
Ссылка: https://hh.ru/vacancy/124635869

Нижний Новгород
1. Специалист отдела по ипотечному страхованию — ЗП не указана
Ссылка: https://hh.ru/vacancy/130247778
"""

WITH_BANNED = """Компания: Ренессанс cтрахование, Группа

Санкт-Петербург
1. Водитель персональный на автомобиле компании — 70000-70000 RUR
Ссылка: https://hh.ru/vacancy/130207683

2. Страховой агент в офис — 70000-100000 RUR
Ссылка: https://hh.ru/vacancy/129721441

3. Специалист по техническому сопровождению клиентов — ЗП не указана
Ссылка: https://hh.ru/vacancy/129890898
"""

WITH_MEDICAL_TITLES = """Компания: Страховая компания

Санкт-Петербург
1. Консультант страховой медицины — ЗП не указана
Ссылка: https://hh.ru/vacancy/100000001

2. Специалист по рассмотрению обращений граждан по вопросам оказания медицинской помощи — ЗП не указана
Ссылка: https://hh.ru/vacancy/100000002

3. Сотрудник контакт-центра СМО по обязательному медицинскому страхованию — ЗП не указана
Ссылка: https://hh.ru/vacancy/100000003

4. Специалист по сопровождению клиентов — ЗП не указана
Ссылка: https://hh.ru/vacancy/100000004
"""


WITH_SPB_ALIAS = """Компания: Тест

Москва:
1. Аналитик — ЗП не указана
Ссылка: https://hh.ru/vacancy/200000001

СПБ:
1. Backend разработчик — ЗП не указана
Ссылка: https://hh.ru/vacancy/200000002

Казань
1. QA инженер — ЗП не указана
Ссылка: https://hh.ru/vacancy/200000003
"""


WITH_COUNT_HEADER = """Санкт-Петербург (2)
1. Специалист по документообороту — ЗП 50 000 - 70 000 ₽
2. Менеджер по развитию партнерской сети — ЗП 150 000 - 500 000 ₽
"""


WITH_COUNTED_MULTI_CITY = """Санкт-Петербург (1)
1. SPB Вакансия — ЗП не указана
Ссылка: https://hh.ru/vacancy/400000001

Москва (1)
1. Moscow Вакансия — ЗП не указана
Ссылка: https://hh.ru/vacancy/400000002
"""


WITH_REMOTE_IN_MOSCOW = """Компания: Удаленка ООО

Москва
1. Python разработчик — удаленная работа, ЗП не указана
Ссылка: https://hh.ru/vacancy/500000001

Санкт-Петербург
1. Аналитик — ЗП не указана
Ссылка: https://hh.ru/vacancy/500000002
"""


class CityExtractTests(unittest.TestCase):
    def test_extract_city_block(self):
        block = extract_city_block(SAMPLE, target_city="Санкт-Петербург")
        self.assertIn("Санкт-Петербург", block)
        self.assertIn("Главный специалист управления ипотечного страхования", block)
        self.assertNotIn("Нижний Новгород", block)

    def test_extract_company_name(self):
        company = extract_company_name(SAMPLE)
        self.assertEqual(company, "Абсолют Страхование")

    def test_extract_spb_vacancies(self):
        items = extract_spb_vacancies(SAMPLE)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].title, "Главный специалист управления ипотечного страхования")
        self.assertEqual(items[0].link, "https://hh.ru/vacancy/130176260")
        self.assertEqual(items[0].company, "Абсолют Страхование")
        self.assertEqual(items[2].link, "https://hh.ru/vacancy/124635869")

    def test_banned_keywords_filter(self):
        items = extract_spb_vacancies(WITH_BANNED)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Специалист по техническому сопровождению клиентов")

    def test_banned_keyword_filters_by_word_part(self):
        items = extract_spb_vacancies(WITH_MEDICAL_TITLES, banned_keywords=("медицин",))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Специалист по сопровождению клиентов")


    def test_extract_spb_vacancies_from_alias_header(self):
        items = extract_spb_vacancies(WITH_SPB_ALIAS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Backend разработчик")
        self.assertEqual(items[0].link, "https://hh.ru/vacancy/200000002")

    def test_parse_spb_vacancies_detected_and_selected(self):
        result = parse_spb_vacancies(WITH_BANNED)
        self.assertEqual(result.detected_items, 3)
        self.assertEqual(len(result.selected_items), 1)

    def test_parse_spb_vacancies_from_inline_links(self):
        inline_links = {
            "специалист по документообороту": "https://hh.ru/vacancy/300000001",
            "менеджер по развитию партнерской сети": "https://hh.ru/vacancy/300000002",
        }
        result = parse_spb_vacancies(WITH_COUNT_HEADER, inline_title_links=inline_links)
        self.assertEqual(result.detected_items, 2)
        self.assertEqual(len(result.selected_items), 2)
        self.assertEqual(result.selected_items[0].link, "https://hh.ru/vacancy/300000001")

    def test_extract_inline_hh_links_from_entities(self):
        text = "1. Специалист по документообороту"

        class _Entity:
            def __init__(self, offset, length, url):
                self.offset = offset
                self.length = length
                self.url = url

        entities = [_Entity(3, len("Специалист по документообороту"), "https://hh.ru/vacancy/300000001")]
        result = extract_inline_hh_links_from_entities(text, entities)
        self.assertEqual(result.get("специалист по документообороту"), "https://hh.ru/vacancy/300000001")

    def test_extract_inline_hh_links_from_entities_with_emoji_utf16_offsets(self):
        text = "🔥 1. Специалист по документообороту"
        title = "Специалист по документообороту"

        class _Entity:
            def __init__(self, offset, length, url):
                self.offset = offset
                self.length = length
                self.url = url

        offset_utf16 = len("🔥 1. ".encode("utf-16-le")) // 2
        length_utf16 = len(title.encode("utf-16-le")) // 2
        entities = [_Entity(offset_utf16, length_utf16, "https://hh.ru/vacancy/300000001")]
        result = extract_inline_hh_links_from_entities(text, entities)
        self.assertEqual(result.get("специалист по документообороту"), "https://hh.ru/vacancy/300000001")

    def test_counted_city_header_is_boundary_for_next_city(self):
        block = extract_city_block(WITH_COUNTED_MULTI_CITY, target_city="Санкт-Петербург")
        self.assertIn("SPB Вакансия", block)
        self.assertNotIn("Москва (1)", block)
        self.assertNotIn("Moscow Вакансия", block)

    def test_parse_spb_vacancies_stops_on_counted_other_city_header(self):
        result = parse_spb_vacancies(WITH_COUNTED_MULTI_CITY)
        self.assertEqual(result.detected_items, 1)
        self.assertEqual(len(result.selected_items), 1)
        self.assertEqual(result.selected_items[0].link, "https://hh.ru/vacancy/400000001")

    def test_remote_vacancy_is_ignored_in_spb_and_selected_in_remote(self):
        spb = parse_spb_vacancies(WITH_REMOTE_IN_MOSCOW)
        self.assertEqual(spb.detected_items, 1)
        self.assertEqual(len(spb.selected_items), 1)
        self.assertEqual(spb.selected_items[0].link, "https://hh.ru/vacancy/500000002")

        remote = parse_remote_vacancies(WITH_REMOTE_IN_MOSCOW)
        self.assertEqual(remote.detected_items, 1)
        self.assertEqual(len(remote.selected_items), 1)
        self.assertEqual(remote.selected_items[0].link, "https://hh.ru/vacancy/500000001")

    def test_no_spb_section(self):
        items = extract_spb_vacancies("Москва\n1. Role\nСсылка: https://hh.ru/vacancy/1")
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
