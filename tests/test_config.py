from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rpd.config import Settings, load_env_file


class SettingsTests(unittest.TestCase):
    def test_defaults_match_mvp_constraints(self) -> None:
        settings = Settings.from_env({})
        self.assertEqual(settings.llm_model, "gpt-5.4")
        self.assertEqual(settings.embedding_model, "text-embedding-3-small")
        self.assertEqual(settings.news_days, 90)
        self.assertEqual(settings.news_max_articles, 100)
        self.assertEqual(settings.request_concurrency, 2)
        self.assertEqual(settings.request_max_retries, 2)

    def test_paths_are_created_and_secrets_are_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            secret = "test-secret-that-must-not-appear"
            settings = Settings.from_env(
                {
                    "RPD_DATA_DIR": temp_dir,
                    "GRAPHRAG_API_KEY": secret,
                    "TAVILY_API_KEY": secret,
                }
            )
            settings.paths.create()
            for path in (
                settings.paths.raw,
                settings.paths.normalized,
                settings.paths.cache,
                settings.paths.database,
                settings.paths.reports,
            ):
                self.assertTrue(path.is_dir())
            self.assertNotIn(secret, repr(settings))
            self.assertEqual(settings.paths.root, Path(temp_dir).resolve())

    def test_local_env_parser_handles_comments_quotes_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text(
                "# local only\nGRAPHRAG_API_KEY='llm-secret'\n"
                'export TAVILY_API_KEY="tavily-secret"\nEMPTY=\n',
                encoding="utf-8",
            )
            self.assertEqual(
                load_env_file(path),
                {
                    "GRAPHRAG_API_KEY": "llm-secret",
                    "TAVILY_API_KEY": "tavily-secret",
                    "EMPTY": "",
                },
            )

    def test_invalid_local_env_entry_is_rejected_without_echoing_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("NOT AN ASSIGNMENT", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1"):
                load_env_file(path)


if __name__ == "__main__":
    unittest.main()
