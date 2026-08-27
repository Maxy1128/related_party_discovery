"""Audit an investigation and regenerate its portable reports."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.reporting import ReportBuilder  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--investigation-id", required=True, type=int)
    args = parser.parse_args()
    settings = Settings.from_env()
    initialize(settings.paths.sqlite_path)

    with connect(settings.paths.sqlite_path) as connection:
        builder = ReportBuilder(connection)
        view = builder.load(args.investigation_id)
        target_id = view.investigation["target_entity_id"]
        evidence_rows = connection.execute(
            """SELECT e.id,e.evidence_text,v.normalized_path
               FROM investigation_assertions ia
               JOIN evidence e ON e.assertion_id=ia.assertion_id
               JOIN document_versions v ON v.id=e.document_version_id
               WHERE ia.investigation_id=? AND e.supports_assertion=1""",
            (args.investigation_id,),
        ).fetchall()
        invalid_evidence = []
        for row in evidence_rows:
            path = Path(row["normalized_path"] or "")
            if not path.is_absolute():
                path = settings.paths.root / path
            if not path.is_file() or row["evidence_text"] not in path.read_text(
                encoding="utf-8", errors="replace"
            ) and connection.execute("SELECT evidence_quality FROM evidence WHERE id=?", (row["id"],)).fetchone()[0] == "EXACT":
                invalid_evidence.append(row["id"])

        extraction_counts = dict(connection.execute(
            """SELECT er.status,COUNT(DISTINCT er.document_version_id)
               FROM extraction_runs er
               JOIN document_versions v ON v.id=er.document_version_id
               JOIN investigation_documents i ON i.document_id=v.document_id
               WHERE i.investigation_id=? AND er.model=? GROUP BY er.status""",
            (args.investigation_id, settings.llm_model),
        ).fetchall())
        graph = builder.graph(view)
        described_entity_ids = {
            row[0] for row in connection.execute(
                """SELECT entity_id FROM entity_descriptions
                   WHERE investigation_id=? AND is_current=1""",
                (args.investigation_id,),
            )
        }
        current_entity_description_values = [
            row[0] for row in connection.execute(
                """SELECT description FROM entity_descriptions
                   WHERE investigation_id=? AND is_current=1""",
                (args.investigation_id,),
            )
        ]
        expected_entity_ids = {target_id}
        for relationship in view.relationships:
            expected_entity_ids.add(relationship["subject_entity_id"])
            if relationship["object_entity_id"] is not None:
                expected_entity_ids.add(relationship["object_entity_id"])
        described_assertion_ids = {
            row[0] for row in connection.execute(
                """SELECT assertion_id FROM relationship_descriptions
                   WHERE investigation_id=? AND is_current=1""",
                (args.investigation_id,),
            )
        }
        current_relationship_description_values = [
            row[0] for row in connection.execute(
                """SELECT description FROM relationship_descriptions
                   WHERE investigation_id=? AND is_current=1""",
                (args.investigation_id,),
            )
        ]
        expected_assertion_ids = {
            row["id"] for row in view.relationships if row["classification"] != "CO_MENTION"
        }
        markdown_path = settings.paths.reports / f"rio_tinto_investigation_{args.investigation_id}.md"
        html_path = settings.paths.reports / f"rio_tinto_investigation_{args.investigation_id}.html"
        markdown_path.write_text(builder.markdown(view), encoding="utf-8")
        html_path.write_text(builder.html(view), encoding="utf-8")
        foreign_key_errors = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        summary = {
            "investigation_id": args.investigation_id,
            "status": view.investigation["status"],
            "model": settings.llm_model,
            "documents": len(view.documents),
            "extraction_documents_by_status": extraction_counts,
            "relationships": len(view.relationships),
            "relationships_by_classification": dict(Counter(
                row["classification"] for row in view.relationships
            )),
            "relationships_by_confidence": dict(Counter(
                row["relationship_confidence"] for row in view.relationships
            )),
            "target_connected_relationships": sum(
                target_id in (row["subject_entity_id"], row["object_entity_id"])
                for row in view.relationships
            ),
            "risk_events": len(view.risk_events),
            "watchlist_matches": len(view.watchlist_matches),
            "supporting_evidence_rows": len(evidence_rows),
            "invalid_evidence_ids": invalid_evidence,
            "current_entity_descriptions": len(described_entity_ids),
            "missing_entity_description_ids": sorted(expected_entity_ids - described_entity_ids),
            "current_relationship_descriptions": len(described_assertion_ids),
            "missing_relationship_description_ids": sorted(
                expected_assertion_ids - described_assertion_ids
            ),
            "entity_descriptions_over_45_words": sum(
                len(value.split()) > 45
                for value in current_entity_description_values
            ),
            "relationship_descriptions_over_40_words": sum(
                len(value.split()) > 40
                for value in current_relationship_description_values
            ),
            "entity_description_versions": connection.execute(
                "SELECT COUNT(*) FROM entity_descriptions WHERE investigation_id=?",
                (args.investigation_id,),
            ).fetchone()[0],
            "relationship_description_versions": connection.execute(
                "SELECT COUNT(*) FROM relationship_descriptions WHERE investigation_id=?",
                (args.investigation_id,),
            ).fetchone()[0],
            "graph_nodes": graph.number_of_nodes(),
            "graph_edges": graph.number_of_edges(),
            "foreign_key_errors": foreign_key_errors,
            "markdown_report": str(markdown_path),
            "html_report": str(html_path),
        }
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
