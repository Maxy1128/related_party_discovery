"""SQLite connection and schema initialization helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
LATEST_SCHEMA_VERSION = 8


class ManagedConnection(sqlite3.Connection):
    """A SQLite connection that closes its file handle after a context block."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(database_path: Path) -> sqlite3.Connection:
    """Open a configured SQLite connection with integrity safeguards enabled."""

    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        database_path, timeout=30, factory=ManagedConnection
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize(database_path: Path) -> None:
    """Create or safely re-open the version-one shared evidence database."""

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(database_path) as connection:
        connection.executescript(schema)
        document_version_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(document_versions)")
        }
        if "raw_content_hash" not in document_version_columns:
            connection.execute(
                "ALTER TABLE document_versions ADD COLUMN raw_content_hash TEXT"
            )
        evidence_columns = {row["name"] for row in connection.execute("PRAGMA table_info(evidence)")}
        if "evidence_quality" not in evidence_columns:
            connection.execute("ALTER TABLE evidence ADD COLUMN evidence_quality TEXT NOT NULL DEFAULT 'EXACT'")
        connection.execute(
            """
            UPDATE document_versions
            SET raw_content_hash = content_hash
            WHERE raw_content_hash IS NULL
            """
        )
        extraction_run_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(extraction_runs)")
        }
        if "cache_source_run_id" not in extraction_run_columns:
            connection.execute(
                "ALTER TABLE extraction_runs ADD COLUMN cache_source_run_id INTEGER REFERENCES extraction_runs(id)"
            )
        mention_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(mentions)")
        }
        mention_additions = {
            "extraction_chunk_id": "INTEGER REFERENCES extraction_chunks(id)",
            "candidate_local_id": "TEXT",
            "resolution_method": "TEXT",
            "resolution_details_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, declaration in mention_additions.items():
            if name not in mention_columns:
                connection.execute(f"ALTER TABLE mentions ADD COLUMN {name} {declaration}")
        connection.executemany(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
            [(version,) for version in range(1, LATEST_SCHEMA_VERSION + 1)],
        )
        from rpd.registry import seed_relation_registry

        seed_relation_registry(connection)
