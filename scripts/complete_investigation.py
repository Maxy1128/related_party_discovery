"""Expand an existing investigation and process its complete available source set."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.descriptions import DescriptionService  # noqa: E402
from rpd.extraction import ExtractionService  # noqa: E402
from rpd.reporting import ReportBuilder  # noqa: E402
from rpd.resolution import EntityResolver  # noqa: E402
from rpd.smoke import RIO_TINTO_SMOKE_DOCUMENTS  # noqa: E402
from rpd.sources.official import OfficialDocumentIngestor  # noqa: E402
from rpd.sources.tavily import NewsDiscoveryService, NewsRepository  # noqa: E402
from rpd.validation import EvidenceMaterializer  # noqa: E402
from rpd.watchlists import WatchlistClient, WatchlistMatcher  # noqa: E402


def attach(connection, investigation_id: int, document_id: int) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO investigation_documents(investigation_id,document_id) VALUES (?,?)",
        (investigation_id, document_id),
    )


def connected_entities(connection, investigation_id: int, target_id: int) -> set[int]:
    values = {target_id}
    for row in connection.execute(
        """SELECT a.subject_entity_id,a.object_entity_id
           FROM investigation_assertions ia JOIN assertions a ON a.id=ia.assertion_id
           WHERE ia.investigation_id=?""", (investigation_id,),
    ):
        values.add(row[0])
        if row[1] is not None:
            values.add(row[1])
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--investigation-id", required=True, type=int)
    parser.add_argument("--max-documents", type=int, default=100)
    parser.add_argument("--max-news-documents", type=int, default=100)
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--skip-risk-lists", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.paths.create()
    initialize(settings.paths.sqlite_path)

    with connect(settings.paths.sqlite_path) as connection:
        investigation = connection.execute(
            "SELECT target_entity_id,title FROM investigations WHERE id=?",
            (args.investigation_id,),
        ).fetchone()
        if not investigation:
            raise LookupError(f"Investigation not found: {args.investigation_id}")
        target = connection.execute(
            "SELECT canonical_name FROM entities WHERE id=?", (investigation["target_entity_id"],)
        ).fetchone()
        company = target[0]

        # Attach every configured Rio Tinto source. Existing content is reused by URL/hash.
        ingestor = OfficialDocumentIngestor(settings, connection)
        official_count = 0
        for fixture in RIO_TINTO_SMOKE_DOCUMENTS:
            try:
                result = ingestor.ingest(fixture.source)
                attach(connection, args.investigation_id, result.document_id)
                official_count += 1
            except Exception as exc:
                print(f"[official skipped] {fixture.source.title}: {type(exc).__name__}: {exc}", flush=True)

        news_count = 0
        if not args.skip_news:
            if not settings.tavily_api_key:
                raise RuntimeError("TAVILY_API_KEY is not configured.")
            articles = NewsDiscoveryService(settings).discover(company)
            repository = NewsRepository(settings, connection)
            for article in articles:
                version_id = repository.persist(article)
                document_id = connection.execute(
                    "SELECT document_id FROM document_versions WHERE id=?", (version_id,)
                ).fetchone()[0]
                attach(connection, args.investigation_id, document_id)
            news_count = len(articles)
        connection.commit()

        rows = connection.execute(
            """SELECT v.id,d.title,d.source_type,v.retrieval_status
               FROM investigation_documents i JOIN documents d ON d.id=i.document_id
               JOIN document_versions v ON v.document_id=d.id AND v.is_current=1
               WHERE i.investigation_id=? AND v.retrieval_status='FULL_TEXT'
               ORDER BY COALESCE(d.published_at,v.retrieved_at) DESC,v.id DESC""",
            (args.investigation_id,),
        ).fetchall()
        news = [r for r in rows if r["source_type"] == "NEWS"][: max(0, min(args.max_news_documents, 100))]
        official = sorted(
            (r for r in rows if r["source_type"] != "NEWS"),
            key=lambda r: r["id"],
        )
        selected = [*news, *official]
        selected = selected[: max(1, min(args.max_documents, 100))]

        if not settings.llm_api_key:
            raise RuntimeError("GRAPHRAG_API_KEY is not configured.")
        extractor = ExtractionService(settings, connection)
        resolver = EntityResolver(settings, connection)
        materializer = EvidenceMaterializer(connection)
        succeeded = failed = 0
        for index, row in enumerate(selected, start=1):
            print(f"[{index}/{len(selected)}] {row['title']}", flush=True)
            try:
                extractor.extract_document(row["id"])
                run = connection.execute(
                    """SELECT id,status FROM extraction_runs WHERE document_version_id=? AND model=?
                       ORDER BY id DESC LIMIT 1""", (row["id"], settings.llm_model),
                ).fetchone()
                if not run or run["status"] != "SUCCEEDED":
                    raise RuntimeError("Extraction did not succeed.")
                resolver.resolve_extraction_run(run["id"])
                materializer.materialize(run["id"])
                connection.execute(
                    """INSERT OR IGNORE INTO investigation_assertions(investigation_id,assertion_id)
                       SELECT ?,id FROM assertions WHERE extraction_run_id=?""",
                    (args.investigation_id, run["id"]),
                )
                succeeded += 1
            except Exception as exc:
                failed += 1
                print(f"  [failed] {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        connection.commit()

        description = DescriptionService(settings, connection).generate(args.investigation_id)
        if not args.skip_risk_lists:
            entity_ids = connected_entities(connection, args.investigation_id, investigation["target_entity_id"])
            matcher = WatchlistMatcher(connection, settings.watchlist_fuzzy_threshold)
            client = WatchlistClient(settings)
            matches = 0
            for list_name in WatchlistClient.URLS:
                snapshot = client.fetch(list_name)
                for entity_id in sorted(entity_ids):
                    matches += len(matcher.match(entity_id, snapshot))
        else:
            matches = 0
        connection.commit()
        view = ReportBuilder(connection).load(args.investigation_id)
        markdown = settings.paths.reports / f"investigation_{args.investigation_id}_complete.md"
        html = settings.paths.reports / f"investigation_{args.investigation_id}_complete.html"
        markdown.write_text(ReportBuilder(connection).markdown(view), encoding="utf-8")
        html.write_text(ReportBuilder(connection).html(view), encoding="utf-8")
        status = "COMPLETED" if failed == 0 else "PARTIAL"
        connection.execute(
            "UPDATE investigations SET status=?,completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (status, args.investigation_id),
        )
        connection.commit()
        print(
            f"Complete investigation finished: official={official_count} news={news_count} "
            f"full_text_processed={succeeded} failed={failed} descriptions="
            f"{description.entity_descriptions + description.relationship_descriptions + description.cached} "
            f"watchlist_matches={matches}\nMarkdown: {markdown}\nHTML: {html}",
            flush=True,
        )


if __name__ == "__main__":
    main()
