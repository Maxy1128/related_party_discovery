"""Generic, resumable investigation orchestration for any selected legal entity."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit

from rpd.config import Settings
from rpd.descriptions import DescriptionService
from rpd.extraction import ExtractionService
from rpd.models import IdentityProfile, OfficialSource
from rpd.repository import IdentityRepository
from rpd.resolution import EntityResolver
from rpd.sources.gleif import GleifClient
from rpd.sources.official import OfficialDocumentIngestor
from rpd.sources.tavily import NewsDiscoveryService, NewsRepository
from rpd.sources.wikidata import WikidataClient
from rpd.validation import EvidenceMaterializer
from rpd.watchlists import WatchlistClient, WatchlistMatcher
from rpd.normalize import normalize_name


ProgressCallback = Callable[[str, str], None]
STEP_NAMES = (
    "Identity", "Official documents", "News", "Extraction", "Descriptions",
    "Risk lists", "Report ready",
)


@dataclass(frozen=True)
class InvestigationRequest:
    company_query: str
    selected_lei: str
    official_urls: tuple[str, ...] = ()
    include_news: bool = True
    include_watchlists: bool = True
    max_extraction_documents: int = 8
    max_news_extraction_documents: int = 3


class InvestigationOrchestrator:
    def __init__(self, settings: Settings, connection: sqlite3.Connection):
        self.settings = settings
        self.connection = connection

    def search_company(self, query: str, limit: int = 10) -> list[IdentityProfile]:
        value = " ".join(query.split())
        if not value:
            raise ValueError("Enter a company name.")
        return GleifClient(self.settings).search_legal_name(value, limit=limit)

    def run(self, request: InvestigationRequest, callback: ProgressCallback | None = None) -> int:
        notify = callback or (lambda _step, _message: None)
        profile = GleifClient(self.settings).get(request.selected_lei, include_parents=True)
        investigation_id = self._create_investigation(request, profile)
        for step in STEP_NAMES:
            self._set_step(investigation_id, step, "PENDING")
        self.connection.commit()
        failures: list[str] = []

        try:
            self._set_step(investigation_id, "Identity", "RUNNING")
            notify("Identity", "Saving the selected GLEIF legal entity.")
            target_id = IdentityRepository(self.connection).upsert(profile)
            query_alias = " ".join(request.company_query.split())
            if query_alias and normalize_name(query_alias) != normalize_name(profile.canonical_name):
                self.connection.execute(
                    """INSERT OR IGNORE INTO entity_aliases(entity_id,alias,normalized_alias,source)
                       VALUES (?,?,?,'INVESTIGATION_QUERY')""",
                    (target_id, query_alias, normalize_name(query_alias)),
                )
            self.connection.execute(
                "UPDATE investigations SET target_entity_id=? WHERE id=?", (target_id, investigation_id)
            )
            self._enrich_wikidata(profile, target_id)
            self._set_step(investigation_id, "Identity", "COMPLETED", 1, f"Selected LEI {profile.lei}.")
            self.connection.commit()
        except Exception as exc:
            self._fail(investigation_id, "Identity", exc)
            self.connection.execute(
                "UPDATE investigations SET status='FAILED',completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (investigation_id,),
            )
            self.connection.commit()
            raise

        version_ids: list[int] = []
        self._set_step(investigation_id, "Official documents", "RUNNING")
        notify("Official documents", "Downloading supplied public disclosures.")
        official_failures = 0
        ingestor = OfficialDocumentIngestor(self.settings, self.connection)
        for index, url in enumerate(self._clean_urls(request.official_urls), start=1):
            try:
                host = (urlsplit(url).hostname or "Official source").removeprefix("www.")
                result = ingestor.ingest(OfficialSource(
                    key=f"user_source_{index}", title=f"Official disclosure {index}",
                    url=url, publisher=host,
                ))
                version_ids.append(result.document_version_id)
                self._attach_document(investigation_id, result.document_id)
            except Exception as exc:
                official_failures += 1
                failures.append(f"Official document {index}: {type(exc).__name__}")
        official_status = "FAILED" if official_failures and not version_ids else "COMPLETED"
        self._set_step(investigation_id, "Official documents", official_status, len(version_ids),
                       f"Stored {len(version_ids)} document(s); {official_failures} failed.")
        self.connection.commit()

        news_count = 0
        if request.include_news and self.settings.tavily_api_key:
            self._set_step(investigation_id, "News", "RUNNING")
            notify("News", "Searching recent English-language news.")
            try:
                articles = NewsDiscoveryService(self.settings).discover(profile.canonical_name)
                repository = NewsRepository(self.settings, self.connection)
                for article in articles:
                    version_id = repository.persist(article)
                    version_ids.append(version_id)
                    document_id = self.connection.execute(
                        "SELECT document_id FROM document_versions WHERE id=?", (version_id,)
                    ).fetchone()[0]
                    self._attach_document(investigation_id, document_id)
                news_count = len(articles)
                self._set_step(investigation_id, "News", "COMPLETED", news_count,
                               f"Stored {news_count} deduplicated article(s).")
            except Exception as exc:
                failures.append(f"News: {type(exc).__name__}")
                self._fail(investigation_id, "News", exc)
        else:
            reason = "News disabled." if not request.include_news else "TAVILY_API_KEY is not configured."
            self._set_step(investigation_id, "News", "SKIPPED", message=reason)
            if request.include_news:
                failures.append("News key unavailable")
        self.connection.commit()

        full_text_ids = self._select_extraction_versions(
            investigation_id,
            request.max_extraction_documents,
            request.max_news_extraction_documents,
        )
        extracted = extraction_failures = 0
        if self.settings.llm_api_key and full_text_ids:
            self._set_step(investigation_id, "Extraction", "RUNNING")
            extractor = ExtractionService(self.settings, self.connection)
            resolver = EntityResolver(self.settings, self.connection)
            materializer = EvidenceMaterializer(self.connection)
            for position, version_id in enumerate(full_text_ids, start=1):
                notify("Extraction", f"Processing document {position} of {len(full_text_ids)}.")
                try:
                    extractor.extract_document(version_id)
                    run_id = self.connection.execute(
                        """SELECT id FROM extraction_runs WHERE document_version_id=? AND model=?
                           ORDER BY id DESC LIMIT 1""", (version_id, self.settings.llm_model)
                    ).fetchone()[0]
                    resolver.resolve_extraction_run(run_id)
                    materializer.materialize(run_id)
                    self.connection.execute(
                        """INSERT OR IGNORE INTO investigation_assertions(investigation_id,assertion_id)
                           SELECT ?,id FROM assertions WHERE extraction_run_id=?""", (investigation_id, run_id)
                    )
                    extracted += 1
                except Exception as exc:
                    extraction_failures += 1
                    failures.append(f"Document {version_id}: {type(exc).__name__}")
            status = "FAILED" if extraction_failures and not extracted else "COMPLETED"
            self._set_step(investigation_id, "Extraction", status, extracted,
                           f"Processed {extracted}; {extraction_failures} failed.")
        else:
            reason = "No full-text documents." if not full_text_ids else "LLM API key is not configured."
            self._set_step(investigation_id, "Extraction", "SKIPPED", message=reason)
            if full_text_ids:
                failures.append("LLM key unavailable")
        self.connection.commit()

        if self.settings.llm_api_key:
            self._set_step(investigation_id, "Descriptions", "RUNNING")
            notify("Descriptions", "Generating evidence-grounded presentation summaries.")
            try:
                result = DescriptionService(self.settings, self.connection).generate(investigation_id)
                total = result.entity_descriptions + result.relationship_descriptions + result.cached
                self._set_step(
                    investigation_id, "Descriptions", "COMPLETED", total,
                    f"Prepared {total} entity and relationship description(s); "
                    f"{result.cached} reused from cache.",
                )
            except Exception as exc:
                failures.append(f"Descriptions: {type(exc).__name__}")
                self._fail(investigation_id, "Descriptions", exc)
        else:
            self._set_step(
                investigation_id, "Descriptions", "SKIPPED",
                message="LLM API key is not configured.",
            )
        self.connection.commit()

        if request.include_watchlists:
            self._set_step(investigation_id, "Risk lists", "RUNNING")
            notify("Risk lists", "Checking resolved entities against public risk lists.")
            try:
                entity_ids = self._investigation_entity_ids(investigation_id, target_id)
                matcher = WatchlistMatcher(self.connection, self.settings.watchlist_fuzzy_threshold)
                client = WatchlistClient(self.settings)
                match_count = 0
                for list_name in ("OFAC", "UK_SANCTIONS", "WORLD_BANK"):
                    snapshot = client.fetch(list_name)
                    for entity_id in entity_ids:
                        match_count += len(matcher.match(entity_id, snapshot))
                self._set_step(investigation_id, "Risk lists", "COMPLETED", match_count,
                               f"Recorded {match_count} potential or confirmed match(es).")
            except Exception as exc:
                failures.append(f"Risk lists: {type(exc).__name__}")
                self._fail(investigation_id, "Risk lists", exc)
        else:
            self._set_step(investigation_id, "Risk lists", "SKIPPED", message="Risk-list checks disabled.")
        final_status = "PARTIAL" if failures else "COMPLETED"
        self._set_step(investigation_id, "Report ready", "COMPLETED", message="Report views are available.")
        self.connection.execute(
            """UPDATE investigations SET status=?,completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (final_status, investigation_id),
        )
        self.connection.commit()
        notify("Report ready", f"Investigation finished with status {final_status}.")
        return investigation_id

    def _create_investigation(self, request: InvestigationRequest, profile: IdentityProfile) -> int:
        parameters = {
            "company_query": request.company_query, "selected_lei": request.selected_lei,
            "official_urls": list(self._clean_urls(request.official_urls)),
            "include_news": request.include_news, "include_watchlists": request.include_watchlists,
            "max_extraction_documents": request.max_extraction_documents,
            "max_news_extraction_documents": request.max_news_extraction_documents,
        }
        return int(self.connection.execute(
            """INSERT INTO investigations(title,status,parameters_json,started_at)
               VALUES (?,'RUNNING',?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            (f"{profile.canonical_name} public-source investigation", json.dumps(parameters)),
        ).lastrowid)

    def _enrich_wikidata(self, gleif_profile: IdentityProfile, entity_id: int) -> None:
        try:
            client = WikidataClient(self.settings)
            for candidate in client.search(gleif_profile.canonical_name, limit=5):
                profile = client.get(candidate["id"])
                if profile.lei and gleif_profile.lei and profile.lei == gleif_profile.lei:
                    IdentityRepository(self.connection).upsert(profile, entity_id=entity_id)
                    return
        except Exception:
            # Wikidata is auxiliary and must never block the authoritative identity step.
            return

    def _set_step(self, investigation_id: int, name: str, status: str,
                  item_count: int = 0, message: str | None = None) -> None:
        self.connection.execute(
            """INSERT INTO investigation_steps(investigation_id,step_name,status,item_count,message,started_at,completed_at)
                VALUES (?,?,?,?,?,CASE WHEN ?='RUNNING' THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') END,
                       CASE WHEN ? IN ('COMPLETED','SKIPPED','FAILED') THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') END)
                ON CONFLICT(investigation_id,step_name)
                DO UPDATE SET status=excluded.status,item_count=excluded.item_count,message=excluded.message,
                  started_at=CASE WHEN excluded.status='RUNNING' THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE investigation_steps.started_at END,
                  completed_at=CASE WHEN excluded.status IN ('COMPLETED','SKIPPED','FAILED') THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE NULL END,
                  updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (investigation_id, name, status, item_count, message, status, status),
        )

    def _fail(self, investigation_id: int, step: str, exc: Exception) -> None:
        self._set_step(investigation_id, step, "FAILED", message=f"{type(exc).__name__}: {str(exc)[:300]}")

    def _attach_document(self, investigation_id: int, document_id: int) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO investigation_documents(investigation_id,document_id) VALUES (?,?)",
            (investigation_id, document_id),
        )

    def _investigation_entity_ids(self, investigation_id: int, target_id: int) -> list[int]:
        values = {target_id}
        for row in self.connection.execute(
            """SELECT a.subject_entity_id,a.object_entity_id FROM investigation_assertions ia
               JOIN assertions a ON a.id=ia.assertion_id WHERE ia.investigation_id=?""",
            (investigation_id,),
        ):
            values.add(row[0])
            if row[1] is not None:
                values.add(row[1])
        return sorted(values)

    def _select_extraction_versions(
        self, investigation_id: int, document_limit: int, news_limit: int
    ) -> list[int]:
        document_limit = max(1, min(int(document_limit), 100))
        news_limit = max(0, min(int(news_limit), document_limit))
        rows = self.connection.execute(
            """SELECT v.id,d.source_type,d.published_at,v.byte_size
               FROM investigation_documents i JOIN documents d ON d.id=i.document_id
               JOIN document_versions v ON v.document_id=d.id AND v.is_current=1
               WHERE i.investigation_id=? AND v.retrieval_status='FULL_TEXT'
               ORDER BY COALESCE(d.published_at,v.retrieved_at) DESC,v.id DESC""",
            (investigation_id,),
        ).fetchall()
        news = [row for row in rows if row["source_type"] == "NEWS"][:news_limit]
        official = sorted(
            (row for row in rows if row["source_type"] != "NEWS"),
            key=lambda row: (row["byte_size"] if row["byte_size"] is not None else 10**12, -row["id"]),
        )
        selected = [*news, *official[: max(0, document_limit - len(news))]]
        return [int(row["id"]) for row in selected[:document_limit]]

    @staticmethod
    def _clean_urls(urls: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(url.strip() for url in urls if url.strip().startswith(("https://", "http://"))))
