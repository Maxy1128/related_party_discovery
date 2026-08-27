from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.http import Download
from rpd.models import NewsArticle
from rpd.sources.tavily import (
    JsonRequestCache,
    NewsDiscoveryService,
    NewsRepository,
    TavilyClient,
)


class FakePostHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, url, payload, headers=None):
        self.calls.append((url, payload, headers))
        return self.response


class SparseTavily:
    def __init__(self):
        self.days = []

    def search(self, query, days, max_results=20):
        self.days.append(days)
        slug = str(abs(hash(query)) % 100000)
        return [
            NewsArticle(
                title=query,
                url=f"https://news.test/{days}/{slug}",
                query=query,
                score=0.5,
                raw_content=f"full text {days} {slug}",
                full_text_source="TAVILY",
                search_days=days,
            )
        ]


class BulkTavily:
    def __init__(self):
        self.calls = 0

    def search(self, query, days, max_results=20):
        self.calls += 1
        slug = str(abs(hash(query)) % 100000)
        return [
            NewsArticle(
                title=f"Article {index}",
                url=f"https://news.test/{slug}/{index}",
                query=query,
                score=1.0 - index / 100,
                raw_content=f"unique text {slug} {index}",
                full_text_source="TAVILY",
                search_days=days,
            )
            for index in range(20)
        ]


class PartlyFailingTavily:
    def search(self, query, days, max_results=20):
        if "ownership" in query:
            raise RuntimeError("one query failed")
        return [
            NewsArticle(
                title=query,
                url=f"https://news.test/{days}/{abs(hash(query))}",
                query=query,
                score=0.7,
                raw_content=f"text {days} {query}",
                full_text_source="TAVILY",
                search_days=days,
            )
        ]


class NeverDownload:
    def download(self, url, max_bytes):
        raise AssertionError("Fallback should not be called for full-text results")


class HtmlFallback:
    def download(self, url, max_bytes):
        return Download(
            final_url=url,
            content=b"<html><script>ignore()</script><body>Recovered article</body></html>",
            content_type="text/html",
            retrieved_at="2026-08-16T00:00:00+00:00",
            headers={},
        )


class TavilyTests(unittest.TestCase):
    def test_client_uses_required_news_parameters_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env(
                {"RPD_DATA_DIR": temp_dir, "TAVILY_API_KEY": "secret-value"}
            )
            http = FakePostHttp(
                {
                    "results": [
                        {
                            "title": "Example",
                            "url": "https://news.test/a",
                            "score": 0.8,
                            "content": "summary",
                            "raw_content": "article",
                            "published_date": "2026-08-15",
                        }
                    ]
                }
            )
            client = TavilyClient(
                settings, http=http, cache=JsonRequestCache(settings.paths.cache)
            )
            first = client.search('"Example" investigation', 90)
            second = client.search('"Example" investigation', 90)
            self.assertEqual(len(http.calls), 1)
            payload = http.calls[0][1]
            self.assertEqual(payload["topic"], "news")
            self.assertEqual(payload["include_raw_content"], "markdown")
            self.assertEqual(payload["max_results"], 20)
            self.assertNotIn("secret-value", str(payload))
            self.assertEqual(first, second)

    def test_sparse_results_expand_to_one_year(self) -> None:
        settings = Settings.from_env({})
        tavily = SparseTavily()
        service = NewsDiscoveryService(
            settings, tavily=tavily, fallback_http=NeverDownload()
        )
        articles = service.discover("Example Company")
        self.assertEqual(set(tavily.days), {90, 180, 365})
        self.assertEqual(len(articles), 18)

    def test_full_first_window_is_capped_at_one_hundred(self) -> None:
        settings = Settings.from_env({})
        tavily = BulkTavily()
        articles = NewsDiscoveryService(
            settings, tavily=tavily, fallback_http=NeverDownload()
        ).discover("Example Company")
        self.assertEqual(tavily.calls, 6)
        self.assertEqual(len(articles), 100)

    def test_one_query_failure_does_not_discard_other_groups(self) -> None:
        settings = Settings.from_env({})
        articles = NewsDiscoveryService(
            settings, tavily=PartlyFailingTavily(), fallback_http=NeverDownload()
        ).discover("Example Company")
        self.assertEqual(len(articles), 15)

    def test_url_tracking_and_content_duplicates_are_removed(self) -> None:
        articles = [
            NewsArticle("A", "https://news.test/a?utm_source=x", "q1", 0.4, raw_content="same"),
            NewsArticle("A2", "https://news.test/a", "q2", 0.9, raw_content="same"),
            NewsArticle("B", "https://news.test/b", "q3", 0.5, raw_content="same"),
        ]
        result = NewsDiscoveryService._deduplicate(articles)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "A2")

    def test_html_fallback_recovers_body_without_scripts(self) -> None:
        settings = Settings.from_env({})
        service = NewsDiscoveryService(
            settings, tavily=SparseTavily(), fallback_http=HtmlFallback()
        )
        article = NewsArticle("A", "https://news.test/a", "q", 0.5)
        recovered = service._fill_missing_content([article])[0]
        self.assertEqual(recovered.full_text_source, "LOCAL_FALLBACK")
        self.assertEqual(recovered.raw_content, "Recovered article")
        self.assertIn(b"<script>ignore()</script>", recovered.source_bytes)

    def test_repository_distinguishes_full_text_from_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env({"RPD_DATA_DIR": temp_dir})
            initialize(settings.paths.sqlite_path)
            with connect(settings.paths.sqlite_path) as connection:
                repository = NewsRepository(settings, connection)
                repository.persist(
                    NewsArticle(
                        "Full",
                        "https://news.test/full",
                        "query",
                        0.9,
                        raw_content="Evidence-capable full text",
                        full_text_source="TAVILY",
                    )
                )
                repository.persist(
                    NewsArticle(
                        "Metadata",
                        "https://news.test/meta",
                        "query",
                        0.5,
                        summary="Snippet only",
                    )
                )
                statuses = [
                    row[0]
                    for row in connection.execute(
                        "SELECT retrieval_status FROM document_versions ORDER BY id"
                    )
                ]
                self.assertEqual(statuses, ["FULL_TEXT", "METADATA_ONLY"])
                metadata_path = connection.execute(
                    "SELECT normalized_path FROM document_versions WHERE retrieval_status='METADATA_ONLY'"
                ).fetchone()[0]
                self.assertIsNone(metadata_path)


if __name__ == "__main__":
    unittest.main()
