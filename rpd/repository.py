"""Persistence operations for identity and document ingestion."""

from __future__ import annotations

import sqlite3

from rpd.models import IdentityProfile
from rpd.normalize import normalize_name


class IdentityRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def upsert(self, profile: IdentityProfile, entity_id: int | None = None) -> int:
        if entity_id is None and profile.lei:
            row = self.connection.execute(
                "SELECT id FROM entities WHERE lei = ?", (profile.lei,)
            ).fetchone()
            entity_id = row["id"] if row else None
        if entity_id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO entities(
                    canonical_name, normalized_name, legal_name, lei,
                    registration_number, registration_authority, country_code,
                    registered_address, website
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.canonical_name,
                    normalize_name(profile.canonical_name),
                    profile.legal_name,
                    profile.lei,
                    profile.registration_number,
                    profile.registration_authority,
                    profile.country_code,
                    profile.registered_address,
                    profile.website,
                ),
            )
            entity_id = int(cursor.lastrowid)
        else:
            self.connection.execute(
                """
                UPDATE entities SET
                    legal_name = COALESCE(?, legal_name),
                    lei = COALESCE(?, lei),
                    registration_number = COALESCE(?, registration_number),
                    registration_authority = COALESCE(?, registration_authority),
                    country_code = COALESCE(?, country_code),
                    registered_address = COALESCE(?, registered_address),
                    website = COALESCE(?, website),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (
                    profile.legal_name,
                    profile.lei,
                    profile.registration_number,
                    profile.registration_authority,
                    profile.country_code,
                    profile.registered_address,
                    profile.website,
                    entity_id,
                ),
            )
        for alias in profile.aliases:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO entity_aliases(
                    entity_id, alias, normalized_alias, source
                ) VALUES (?, ?, ?, ?)
                """,
                (entity_id, alias, normalize_name(alias), profile.source),
            )
        for scheme, value in profile.identifiers:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO entity_identifiers(
                    entity_id, identifier_scheme, identifier_value, source, source_url
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (entity_id, scheme, value, profile.source, profile.source_url),
            )
        return entity_id
