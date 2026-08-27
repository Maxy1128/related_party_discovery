"""Retry failed documents and refresh governed outputs for an investigation."""

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
from rpd.resolution import EntityResolver  # noqa: E402
from rpd.validation import EvidenceMaterializer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--investigation-id", required=True, type=int)
    args = parser.parse_args()
    settings = Settings.from_env()
    initialize(settings.paths.sqlite_path)
    with connect(settings.paths.sqlite_path) as connection:
        rows = connection.execute(
            """SELECT er.id run_id,er.document_version_id,er.status,d.title
               FROM extraction_runs er JOIN document_versions v ON v.id=er.document_version_id
               JOIN documents d ON d.id=v.document_id
               JOIN investigation_documents i ON i.document_id=d.id
               WHERE i.investigation_id=? AND er.model=?
               ORDER BY CASE er.status WHEN 'FAILED' THEN 0 ELSE 1 END,er.id""",
            (args.investigation_id, settings.llm_model),
        ).fetchall()
        extractor = ExtractionService(settings, connection)
        resolver = EntityResolver(settings, connection)
        materializer = EvidenceMaterializer(connection)
        succeeded = failed = 0
        for row in rows:
            print(f"[{row['status']}] {row['title']}", flush=True)
            try:
                if row["status"] == "FAILED":
                    extractor.extract_document(row["document_version_id"])
                refreshed = connection.execute(
                    """SELECT id,status FROM extraction_runs WHERE document_version_id=?
                       AND model=? ORDER BY id DESC LIMIT 1""",
                    (row["document_version_id"], settings.llm_model),
                ).fetchone()
                if refreshed["status"] != "SUCCEEDED":
                    raise RuntimeError("Extraction did not succeed.")
                resolver.resolve_extraction_run(refreshed["id"])
                materializer.materialize(refreshed["id"])
                connection.execute(
                    """INSERT OR IGNORE INTO investigation_assertions(investigation_id,assertion_id)
                       SELECT ?,id FROM assertions WHERE extraction_run_id=?""",
                    (args.investigation_id, refreshed["id"]),
                )
                succeeded += 1
            except Exception as exc:
                failed += 1
                print(f"[FAILED] {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        description_failed = False
        if succeeded and settings.llm_api_key:
            try:
                result = DescriptionService(settings, connection).generate(args.investigation_id)
                total = result.entity_descriptions + result.relationship_descriptions + result.cached
                connection.execute(
                    """INSERT INTO investigation_steps(investigation_id,step_name,status,item_count,
                       message,completed_at) VALUES (?,'Descriptions','COMPLETED',?,?,
                       strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                       ON CONFLICT(investigation_id,step_name) DO UPDATE SET
                         status='COMPLETED',item_count=excluded.item_count,message=excluded.message,
                         completed_at=excluded.completed_at,
                         updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (args.investigation_id, total,
                     f"Prepared {total} entity and relationship description(s); "
                     f"{result.cached} reused from cache."),
                )
            except Exception as exc:
                description_failed = True
                print(f"[FAILED] Descriptions: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
                connection.execute(
                    """INSERT INTO investigation_steps(investigation_id,step_name,status,message,
                       completed_at) VALUES (?,'Descriptions','FAILED',?,
                       strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                       ON CONFLICT(investigation_id,step_name) DO UPDATE SET
                         status='FAILED',message=excluded.message,completed_at=excluded.completed_at,
                         updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (args.investigation_id, f"{type(exc).__name__}: {str(exc)[:300]}"),
                )
        connection.execute(
            """UPDATE investigation_steps SET status=?,item_count=?,message=?,
               completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE investigation_id=? AND step_name='Extraction'""",
            ("COMPLETED" if succeeded else "FAILED", succeeded,
             f"Processed {succeeded}; {failed} failed after retry.", args.investigation_id),
        )
        connection.execute(
            "UPDATE investigations SET status=? WHERE id=?",
            ("COMPLETED" if failed == 0 and not description_failed else "PARTIAL",
             args.investigation_id),
        )
        connection.commit()
    print(f"Reprocessing complete: succeeded={succeeded} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
