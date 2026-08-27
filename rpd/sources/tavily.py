"""Generic Tavily news discovery, fallback extraction, deduplication, and cache."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from rpd.config import Settings
from rpd.http import PublicHttpClient
from rpd.models import NewsArticle
from rpd.normalize import html_to_text, normalize_url


QUERY_GROUPS = (
    "ownership acquisition investment",
    "joint venture partnership agreement",
    "supplier customer contractor",
    "investigation lawsuit sanctions",
    "fraud corruption money laundering",
    "environment human rights",
)


class JsonRequestCache:
    """Daily request cache whose keys never contain API credentials."""

    def __init__(self, root: Path):
        self.root = root / "tavily"

    @staticmethod
    def key(payload: dict) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def get(self, payload: dict) -> dict | None:
        path = self.root / f"{self.key(payload)}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, payload: dict, response: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{self.key(payload)}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(response, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(path)


class TavilyClient:
    def __init__(
        self,
        settings: Settings,
        http: PublicHttpClient | None = None,
        cache: JsonRequestCache | None = None,
    ):
        self.settings = settings
        self.http = http or PublicHttpClient(settings)
        self.cache = cache or JsonRequestCache(settings.paths.cache)

    def search(self, query: str, days: int, max_results: int = 20) -> list[NewsArticle]:
        start_date = (date.today() - timedelta(days=days)).isoformat()
        payload = {
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": max(1, min(max_results, 20)),
            "start_date": start_date,
            "include_answer": False,
            "include_raw_content": "markdown",
            "include_images": False,
            "auto_parameters": False,
        }
        response = self.cache.get(payload)
        if response is None:
            api_key = self.settings.require_tavily_key()
            response = self.http.post_json(
                self.settings.tavily_search_url,
                payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            self.cache.put(payload, response)
        articles = []
        for item in response.get("results", []):
            url = item.get("url")
            if not url:
                continue
            raw_content = item.get("raw_content") or None
            articles.append(
                NewsArticle(
                    title=item.get("title") or url,
                    url=url,
                    query=query,
                    score=float(item.get("score") or 0.0),
                    published_at=item.get("published_date"),
                    summary=item.get("content") or "",
                    raw_content=raw_content,
                    full_text_source="TAVILY" if raw_content else "METADATA_ONLY",
                    search_days=days,
                )
            )
        return articles


class NewsDiscoveryService:
    def __init__(
        self,
        settings: Settings,
        tavily: TavilyClient | None = None,
        fallback_http: PublicHttpClient | None = None,
    ):
        self.settings = settings
        self.tavily = tavily or TavilyClient(settings)
        self.fallback_http = fallback_http or PublicHttpClient(settings)

    def discover(self, company_name: str) -> list[NewsArticle]:
        company_name = " ".join(company_name.split())
        if not company_name:
            raise ValueError("Company name is required for news discovery.")
        collected: list[NewsArticle] = []
        for days in (self.settings.news_days, 180, 365):
            queries = [f'"{company_name}" {group}' for group in QUERY_GROUPS]
            failures: list[Exception] = []
            with ThreadPoolExecutor(
                max_workers=self.settings.request_concurrency
            ) as executor:
                futures = {
                    executor.submit(self.tavily.search, query, days): query
                    for query in queries
                }
                for future in as_completed(futures):
                    try:
                        collected.extend(future.result())
                    except Exception as exc:
                        failures.append(exc)
            if len(failures) == len(queries) and not collected:
                raise RuntimeError("All Tavily news query groups failed.") from failures[0]
            articles = self._deduplicate(collected)
            articles = self._fill_missing_content(
                articles[: self.settings.news_max_articles]
            )
            if self._full_text_count(articles) >= 20 or days == 365:
                return articles[: self.settings.news_max_articles]
        return []

    def _fill_missing_content(self, articles: Iterable[NewsArticle]) -> list[NewsArticle]:
        items = list(articles)
        with ThreadPoolExecutor(max_workers=self.settings.request_concurrency) as executor:
            return list(executor.map(self._fill_one, items))

    def _fill_one(self, article: NewsArticle) -> NewsArticle:
        if article.raw_content:
            return article
        try:
            download = self.fallback_http.download(article.url, max_bytes=10 * 1024 * 1024)
            if download.content_type not in ("text/html", "application/xhtml+xml", ""):
                return article
            text = html_to_text(download.content)
            if text:
                return replace(
                    article,
                    raw_content=text,
                    full_text_source="LOCAL_FALLBACK",
                    source_bytes=download.content,
                    source_media_type=download.content_type or "text/html",
                )
        except Exception:
            # Paywalls, robots controls, login pages, and fetch failures remain
            # metadata-only; this client never attempts to bypass them.
            pass
        return article

    @staticmethod
    def _full_text_count(articles: Iterable[NewsArticle]) -> int:
        return sum(bool(article.raw_content) for article in articles)

    @staticmethod
    def _deduplicate(articles: Iterable[NewsArticle]) -> list[NewsArticle]:
        by_url: dict[str, NewsArticle] = {}
        for article in articles:
            key = normalize_url(article.url)
            current = by_url.get(key)
            if current is None or NewsDiscoveryService._rank(article) > NewsDiscoveryService._rank(current):
                by_url[key] = article
        by_content: dict[str, NewsArticle] = {}
        without_content: list[NewsArticle] = []
        for article in by_url.values():
            if not article.raw_content:
                without_content.append(article)
                continue
            key = hashlib.sha256(article.raw_content.strip().encode("utf-8")).hexdigest()
            current = by_content.get(key)
            if current is None or NewsDiscoveryService._rank(article) > NewsDiscoveryService._rank(current):
                by_content[key] = article
        return sorted(
            [*by_content.values(), *without_content],
            key=NewsDiscoveryService._rank,
            reverse=True,
        )

    @staticmethod
    def _rank(article: NewsArticle) -> tuple[int, float, str]:
        return (1 if article.raw_content else 0, article.score, article.published_at or "")


def publisher_from_url(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


class NewsRepository:
    """Persist Tavily results without treating metadata-only text as evidence."""

    def __init__(self, settings: Settings, connection: sqlite3.Connection):
        self.settings = settings
        self.connection = connection

    def persist(self, article: NewsArticle) -> int:
        normalized_article_url = normalize_url(article.url)
        now = datetime.now(timezone.utc).isoformat()
        row = self.connection.execute(
            "SELECT id FROM documents WHERE source_type='NEWS' AND normalized_url=?",
            (normalized_article_url,),
        ).fetchone()
        if row:
            document_id = int(row["id"])
        else:
            document_id = int(
                self.connection.execute(
                    """
                    INSERT INTO documents(
                        source_type,title,publisher,original_url,normalized_url,
                        published_at,first_retrieved_at
                    ) VALUES ('NEWS',?,?,?,?,?,?)
                    """,
                    (
                        article.title,
                        publisher_from_url(article.url),
                        article.url,
                        normalized_article_url,
                        article.published_at,
                        now,
                    ),
                ).lastrowid
            )
        full_text = article.raw_content or ""
        if full_text:
            normalized_bytes = full_text.encode("utf-8")
            stored_bytes = article.source_bytes or normalized_bytes
            retrieval_status = "FULL_TEXT"
            suffix = ".html" if article.source_bytes else ".md"
        else:
            stored_bytes = json.dumps(
                {"title": article.title, "url": article.url, "summary": article.summary},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            retrieval_status = "METADATA_ONLY"
            suffix = ".json"
            normalized_bytes = stored_bytes
        raw_content_hash = hashlib.sha256(stored_bytes).hexdigest()
        content_hash = hashlib.sha256(normalized_bytes).hexdigest()
        raw_path = (
            self.settings.paths.raw
            / "news"
            / raw_content_hash[:2]
            / f"{raw_content_hash}{suffix}"
        )
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists():
            raw_path.write_bytes(stored_bytes)
        normalized_path = None
        if full_text:
            normalized_path = (
                self.settings.paths.normalized
                / "news"
                / content_hash[:2]
                / f"{content_hash}.txt"
            )
            normalized_path.parent.mkdir(parents=True, exist_ok=True)
            if not normalized_path.exists():
                normalized_path.write_text(full_text, encoding="utf-8")
        version = self.connection.execute(
            "SELECT id FROM document_versions WHERE document_id=? AND content_hash=?",
            (document_id, content_hash),
        ).fetchone()
        if version:
            version_id = int(version["id"])
        else:
            self.connection.execute(
                "UPDATE document_versions SET is_current=0 WHERE document_id=?",
                (document_id,),
            )
            version_id = int(
                self.connection.execute(
                    """
                    INSERT INTO document_versions(
                        document_id,content_hash,raw_content_hash,media_type,byte_size,
                        raw_path,normalized_path,retrieval_status,retrieved_at,is_current
                    ) VALUES (?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        document_id,
                        content_hash,
                        raw_content_hash,
                        article.source_media_type
                        or ("text/markdown" if full_text else "application/json"),
                        len(stored_bytes),
                        raw_path.relative_to(self.settings.paths.root).as_posix(),
                        normalized_path.relative_to(self.settings.paths.root).as_posix()
                        if normalized_path
                        else None,
                        retrieval_status,
                        now,
                    ),
                ).lastrowid
            )
        self.connection.execute(
            """
            INSERT INTO news_search_results(
                document_id,query,search_days,tavily_score,full_text_source,discovered_at
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(document_id,query,search_days) DO UPDATE SET
                tavily_score=excluded.tavily_score,
                full_text_source=excluded.full_text_source,
                discovered_at=excluded.discovered_at
            """,
            (
                document_id,
                article.query,
                article.search_days,
                article.score,
                article.full_text_source,
                now,
            ),
        )
        return version_id
