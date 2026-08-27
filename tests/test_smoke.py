from __future__ import annotations

import unittest

from rpd.normalize import normalize_url
from rpd.smoke import RIO_TINTO_SMOKE_DOCUMENTS


class SmokeFixtureTests(unittest.TestCase):
    def test_fixture_has_eight_unique_official_documents(self):
        self.assertEqual(len(RIO_TINTO_SMOKE_DOCUMENTS), 8)
        urls = [normalize_url(item.source.url) for item in RIO_TINTO_SMOKE_DOCUMENTS]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertTrue(all(item.source.source_type == "OFFICIAL_DOCUMENT" for item in RIO_TINTO_SMOKE_DOCUMENTS))

    def test_fixture_includes_current_and_retained_annual_reports(self):
        keys = {item.source.key for item in RIO_TINTO_SMOKE_DOCUMENTS}
        self.assertIn("annual_report_2024", keys)
        self.assertIn("annual_report_2025", keys)


if __name__ == "__main__":
    unittest.main()
