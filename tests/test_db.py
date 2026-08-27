from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from rpd.db import connect, initialize


EXPECTED_TABLES = {
    "assertions",
    "document_versions",
    "documents",
    "embeddings",
    "entities",
    "entity_descriptions",
    "entity_aliases",
    "entity_identifiers",
    "evidence",
    "extracted_entity_candidates",
    "extraction_runs",
    "extraction_chunks",
    "investigation_assertions",
    "investigation_documents",
    "investigation_steps",
    "investigations",
    "mentions",
    "news_search_results",
    "relation_type_registry",
    "relationship_descriptions",
    "risk_events",
    "schema_migrations",
    "watchlist_matches",
}


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "evidence.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialization_is_idempotent_and_complete(self) -> None:
        initialize(self.database_path)
        initialize(self.database_path)
        with connect(self.database_path) as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables))
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                8,
            )
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertGreaterEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM relation_type_registry WHERE registry_status='ACTIVE'"
                ).fetchone()[0],
                18,
            )

    def test_confidence_constraint_rejects_unknown_value(self) -> None:
        initialize(self.database_path)
        with connect(self.database_path) as connection:
            entity_id = connection.execute(
                "INSERT INTO entities(canonical_name, normalized_name) VALUES (?, ?)",
                ("Rio Tinto plc", "rio tinto plc"),
            ).lastrowid
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO assertions(
                        subject_entity_id, normalized_relation_type, classification,
                        assertion_text, explicit_or_inferred, relationship_confidence
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        "OTHER_MATERIAL_RELATION",
                        "COUNTERPARTY",
                        "Invalid confidence test",
                        "EXPLICIT",
                        "CERTAIN",
                    ),
                )

    def test_database_does_not_persist_environment_secret(self) -> None:
        initialize(self.database_path)
        secret = b"test-secret-that-must-not-appear"
        self.assertNotIn(secret, self.database_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
