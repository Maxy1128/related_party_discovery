from __future__ import annotations

import hashlib
import json
import tempfile
import unittest

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.extraction_schema import EntityCandidate
from rpd.resolution import EntityResolver


def candidate(local_id, name, identifiers=None, aliases=None, ambiguous=False):
    return {
        "local_id": local_id,
        "name": name,
        "entity_type": "ORGANIZATION",
        "entity_scope": "LEGAL_ENTITY",
        "aliases": aliases or [],
        "identifiers": identifiers or [],
        "country_code": "GB",
        "ambiguous": ambiguous,
        "ambiguity_flags": [],
    }


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings.from_env({"RPD_DATA_DIR": self.temp.name})
        self.settings.paths.create()
        initialize(self.settings.paths.sqlite_path)

    def tearDown(self):
        self.temp.cleanup()

    def _make_run(self, connection):
        text = "Acme Holdings Ltd signed with New Partner."
        digest = hashlib.sha256(text.encode()).hexdigest()
        path = self.settings.paths.normalized / "test.txt"
        path.write_text(text, encoding="utf-8")
        document_id = connection.execute(
            """INSERT INTO documents(source_type,title,normalized_url,first_retrieved_at)
               VALUES ('TEST','Test','https://test/doc','2026-08-16T00:00:00Z')"""
        ).lastrowid
        version_id = connection.execute(
            """INSERT INTO document_versions(
               document_id,content_hash,raw_content_hash,normalized_path,
               retrieval_status,retrieved_at) VALUES (?,?,?,?,?,?)""",
            (document_id, digest, digest, "normalized/test.txt", "FULL_TEXT", "2026-08-16T00:00:00Z"),
        ).lastrowid
        run_id = connection.execute(
            """INSERT INTO extraction_runs(
               document_version_id,model,prompt_version,schema_version,status,response_json)
               VALUES (?,'gpt-5.4','relationship_extraction_v1','1','SUCCEEDED','{}')""",
            (version_id,),
        ).lastrowid
        payload = {
            "entities": [
                candidate("e1", "Acme Holdings Ltd", [{"scheme": "LEI", "value": "LEI-ACME"}]),
                candidate("e2", "New Partner"),
            ],
            "mentions": [
                {"entity_local_id": "e1", "mention_text": "Acme Holdings Ltd", "context_text": text, "evidence_text": text},
                {"entity_local_id": "e2", "mention_text": "New Partner", "context_text": text, "evidence_text": text},
            ],
            "relationships": [], "risk_events": [], "document_date": None,
            "ambiguity_flags": [],
        }
        chunk_id = connection.execute(
            """INSERT INTO extraction_chunks(
               extraction_run_id,chunk_index,start_offset,end_offset,content_hash,status,response_json)
               VALUES (?,0,0,?,?, 'SUCCEEDED',?)""",
            (run_id, len(text), digest, json.dumps(payload)),
        ).lastrowid
        return run_id, chunk_id, text

    def test_identifier_match_and_ambiguous_group_are_persisted_idempotently(self):
        with connect(self.settings.paths.sqlite_path) as connection:
            existing_id = connection.execute(
                """INSERT INTO entities(
                   canonical_name,normalized_name,legal_name,entity_scope,entity_type,
                   lei,country_code) VALUES (?,?,?,?,?,?,?)""",
                ("ACME HOLDINGS LTD", "acme holdings ltd", "ACME HOLDINGS LTD",
                 "LEGAL_ENTITY", "ORGANIZATION", "LEI-ACME", "GB"),
            ).lastrowid
            connection.execute(
                """INSERT INTO entity_identifiers(
                   entity_id,identifier_scheme,identifier_value,source)
                   VALUES (?,'LEI','LEI-ACME','GLEIF')""",
                (existing_id,),
            )
            run_id, _, text = self._make_run(connection)
            resolver = EntityResolver(self.settings, connection)
            self.assertEqual(resolver.resolve_extraction_run(run_id), {"candidates": 2, "mentions": 2})
            rows = connection.execute(
                """SELECT local_id,resolved_entity_id,resolution_status,resolution_method
                   FROM extracted_entity_candidates ORDER BY local_id"""
            ).fetchall()
            self.assertEqual(rows[0]["resolved_entity_id"], existing_id)
            self.assertEqual(rows[0]["resolution_method"], "IDENTIFIER_EXACT")
            self.assertEqual(rows[1]["resolution_status"], "GROUP_LEVEL")
            group = connection.execute(
                "SELECT entity_scope,ambiguous FROM entities WHERE id=?",
                (rows[1]["resolved_entity_id"],),
            ).fetchone()
            self.assertEqual(tuple(group), ("GROUP", 1))
            offsets = [tuple(row) for row in connection.execute(
                "SELECT mention_text,start_offset,end_offset FROM mentions ORDER BY start_offset"
            )]
            self.assertEqual(offsets[0], ("Acme Holdings Ltd", 0, 17))
            self.assertEqual(offsets[1][1], text.index("New Partner"))
            entity_count = connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            resolver.resolve_extraction_run(run_id)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0], entity_count)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mentions").fetchone()[0], 2)

    def test_exact_alias_resolves_without_fuzzy_matching(self):
        with connect(self.settings.paths.sqlite_path) as connection:
            entity_id = connection.execute(
                "INSERT INTO entities(canonical_name,normalized_name) VALUES ('Example Corporation','example corporation')"
            ).lastrowid
            connection.execute(
                """INSERT INTO entity_aliases(entity_id,alias,normalized_alias,source)
                   VALUES (?,'Example Corp','example corp','OFFICIAL')""",
                (entity_id,),
            )
            decision = EntityResolver(self.settings, connection).resolve_candidate(
                EntityCandidate.model_validate(candidate("e1", "Example Corp"))
            )
            self.assertEqual(decision.entity_id, entity_id)
            self.assertEqual(decision.method, "NAME_EXACT")

    def test_strong_identifier_creates_non_ambiguous_legal_entity(self):
        with connect(self.settings.paths.sqlite_path) as connection:
            decision = EntityResolver(self.settings, connection).resolve_candidate(
                EntityCandidate.model_validate(
                    candidate("e1", "New Legal Ltd", [{"scheme": "LEI", "value": "NEW-LEI"}])
                )
            )
            row = connection.execute(
                "SELECT entity_scope,ambiguous,lei FROM entities WHERE id=?", (decision.entity_id,)
            ).fetchone()
            self.assertEqual(tuple(row), ("LEGAL_ENTITY", 0, "NEW-LEI"))
            self.assertEqual(decision.method, "NEW_IDENTIFIED_ENTITY")

    def test_investigation_alias_wins_over_an_ambiguous_duplicate_group(self):
        with connect(self.settings.paths.sqlite_path) as connection:
            legal_id = connection.execute(
                """INSERT INTO entities(canonical_name,normalized_name,entity_scope,ambiguous)
                   VALUES ('Example Holdings plc','example holdings plc','LEGAL_ENTITY',0)"""
            ).lastrowid
            connection.execute(
                """INSERT INTO entity_aliases(entity_id,alias,normalized_alias,source)
                   VALUES (?,'Example Holdings','example holdings','INVESTIGATION_QUERY')""",
                (legal_id,),
            )
            connection.execute(
                """INSERT INTO entities(canonical_name,normalized_name,entity_scope,ambiguous)
                   VALUES ('Example Holdings','example holdings','GROUP',1)"""
            )
            decision = EntityResolver(self.settings, connection).resolve_candidate(
                EntityCandidate.model_validate(
                    candidate("e1", "Example Holdings", ambiguous=True)
                )
            )
            self.assertEqual(decision.entity_id, legal_id)
            self.assertEqual(decision.method, "INVESTIGATION_ALIAS")


if __name__ == "__main__":
    unittest.main()
