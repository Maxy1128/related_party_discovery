"""Read-only Streamlit UI smoke tests against the populated local database."""

from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StreamlitUiTests(unittest.TestCase):
    def test_english_navigation_and_new_investigation_form(self):
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Explore the relationship network")
        self.assertEqual(
            tuple(app.sidebar.radio[0].options),
            ("Network Explorer", "New Investigation", "Processing Status", "Investigation Report", "Shared Evidence"),
        )
        app.sidebar.radio[0].set_value("New Investigation").run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Discover public corporate relationships")
        self.assertEqual(app.text_input[0].label, "Company name")
        self.assertTrue(app.button[0].disabled)

    def test_existing_report_and_shared_evidence_render(self):
        app = AppTest.from_file(str(PROJECT_ROOT / "app.py")).run(timeout=15)
        app.sidebar.radio[0].set_value("Investigation Report").run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Evidence-backed findings")
        self.assertIn("RIO TINTO PLC", [item.value for item in app.subheader])
        self.assertGreaterEqual(len(app.info), 1)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            ["Related Parties", "Counterparties", "Risk Alerts", "Timeline", "Network", "Evidence"],
        )
        self.assertEqual(len(app.get("download_button")), 2)

        app.sidebar.radio[0].set_value("Shared Evidence").run(timeout=15)
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Reusable public-source records")
        self.assertGreaterEqual(len(app.dataframe), 2)


if __name__ == "__main__":
    unittest.main()
