import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / "app"))

import unittest

from city_extract import extract_city_block, extract_spb_vacancies


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


class CityExtractTests(unittest.TestCase):
    def test_extract_city_block(self):
        block = extract_city_block(SAMPLE, target_city="Санкт-Петербург")
        self.assertIn("Санкт-Петербург", block)
        self.assertIn("Главный специалист управления ипотечного страхования", block)
        self.assertNotIn("Нижний Новгород", block)

    def test_extract_spb_vacancies(self):
        items = extract_spb_vacancies(SAMPLE)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].title, "Главный специалист управления ипотечного страхования")
        self.assertEqual(items[0].link, "https://hh.ru/vacancy/130176260")
        self.assertEqual(items[2].link, "https://hh.ru/vacancy/124635869")

    def test_no_spb_section(self):
        items = extract_spb_vacancies("Москва\n1. Role\nСсылка: https://hh.ru/vacancy/1")
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
