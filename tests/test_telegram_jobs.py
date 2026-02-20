import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "app"))

import unittest

from telegram_jobs import _effective_source_fetch_limit


class TelegramJobsTests(unittest.TestCase):
    def test_effective_source_fetch_limit_keeps_positive_value(self):
        self.assertEqual(_effective_source_fetch_limit(120), 120)

    def test_effective_source_fetch_limit_fallback_for_invalid_values(self):
        self.assertEqual(_effective_source_fetch_limit(0), 80)
        self.assertEqual(_effective_source_fetch_limit(-5), 80)
        self.assertEqual(_effective_source_fetch_limit("bad"), 80)
        self.assertEqual(_effective_source_fetch_limit(None), 80)


if __name__ == "__main__":
    unittest.main()
