from __future__ import annotations

import hashlib
import json
import tempfile
import unittest

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.registry import resolve_relation_type, seed_relation_registry
from rpd.validation import EvidenceMaterializer, relationship_confidence, risk_severity


class RegistryAndValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings.from_env({"RPD_DATA_DIR": self.temp.name})
        self.settings.paths.create()
        initialize(self.settings.paths.sqlite_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_registry_is_governed_and_idempotent(self):
        with connect(self.settings.paths.sqlite_path) as connection:
            seed_relation_registry(connection)
            count = connection.execute("SELECT COUNT(*) FROM relation_type_registry").fetchone()[0]
            seed_relation_registry(connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM relation_type_registry").fetchone()[0], count)
            _, canonical = resolve_relation_type(connection, "OFFTAKE_AGREEMENT_WITH")
            self.assertEqual(canonical, "OTHER_MATERIAL_RELATION")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM relation_type_registry WHERE canonical_name='OFFTAKE_AGREEMENT_WITH'"
            ).fetchone()[0], 0)
            for alias, expected in (
                ("JOINT_VENTURE", "JOINT_VENTURE_WITH"),
                ("WORKS_WITH", "PARTNERED_WITH"),
                ("MEMORANDUM_OF_UNDERSTANDING", "MEMORANDUM_OF_UNDERSTANDING_WITH"),
                ("OPERATES", "OPERATES"),
                ("GOVERNMENT_PARTNERSHIP", "PARTNERED_WITH"),
                ("EQUITY_OWNERSHIP", "OWNS"),
                ("POWER_OFFTAKE_AGREEMENT", "POWER_PURCHASE_AGREEMENT_WITH"),
                ("OPERATIONAL_SUPPLY_CONNECTION", "CUSTOMER_OF"),
            ):
                self.assertEqual(resolve_relation_type(connection, alias)[1], expected)

    def test_confidence_and_severity_are_independent_rules(self):
        self.assertEqual(relationship_confidence("OFFICIAL_DOCUMENT", "EXPLICIT", "RELATED_PARTY"), "HIGH")
        self.assertEqual(relationship_confidence("NEWS", "EXPLICIT", "COUNTERPARTY"), "LOW")
        self.assertEqual(relationship_confidence("OFFICIAL_DOCUMENT", "INFERRED", "RELATED_PARTY"), "UNVERIFIED")
        self.assertEqual(relationship_confidence("NEWS", "EXPLICIT", "CO_MENTION"), "UNVERIFIED")
        self.assertEqual(
            relationship_confidence(
                "OFFICIAL_DOCUMENT", "EXPLICIT", "COUNTERPARTY",
                "OTHER_MATERIAL_RELATION",
            ),
            "LOW",
        )
        self.assertEqual(risk_severity("SANCTIONS_DESIGNATION"), "CRITICAL")
        self.assertEqual(risk_severity("ENVIRONMENTAL_INCIDENT"), "LOW")

    def test_materialization_preserves_evidence_and_dates(self):
        payload = {
            "entities": [
                {"local_id": "e1", "name": "Alpha plc", "entity_type": "ORGANIZATION", "entity_scope": "LEGAL_ENTITY", "aliases": [], "identifiers": [], "country_code": "GB", "ambiguous": False, "ambiguity_flags": []},
                {"local_id": "e2", "name": "Beta Ltd", "entity_type": "ORGANIZATION", "entity_scope": "LEGAL_ENTITY", "aliases": [], "identifiers": [], "country_code": "GB", "ambiguous": False, "ambiguity_flags": []},
            ],
            "mentions": [],
            "relationships": [{
                "subject_local_id": "e1", "object_local_id": "e2", "subject_role": "parent", "object_role": "subsidiary",
                "classification": "RELATED_PARTY", "normalized_relation_type": "PARENT_OF", "proposed_relation_type": None,
                "explicit_or_inferred": "EXPLICIT", "event_date": "2026-01-02", "valid_from": "2026-01-01", "valid_to": None,
                "evidence_text": "Alpha plc directly controls Beta Ltd.", "ambiguity_flags": [],
            }],
            "risk_events": [{
                "entity_local_id": "e2", "event_type": "REGULATORY_INVESTIGATION", "description": "A regulator opened an investigation.",
                "event_date": "2026-02-01", "risk_severity_candidate": "HIGH", "evidence_text": "The regulator opened an investigation into Beta Ltd.", "ambiguity_flags": [],
            }],
            "document_date": "2026-03-01", "ambiguity_flags": [],
        }
        with connect(self.settings.paths.sqlite_path) as connection:
            ids = [connection.execute(
                "INSERT INTO entities(canonical_name,normalized_name,country_code) VALUES (?,?,?)", (name, name.casefold(), "GB")
            ).lastrowid for name in ("Alpha plc", "Beta Ltd")]
            document_id = connection.execute(
                "INSERT INTO documents(source_type,title,normalized_url,published_at,first_retrieved_at) VALUES ('OFFICIAL_DOCUMENT','Annual report','https://example.test/report','2026-03-01','2026-03-02')"
            ).lastrowid
            version_id = connection.execute(
                "INSERT INTO document_versions(document_id,content_hash,retrieval_status,retrieved_at) VALUES (?,?,?,?)",
                (document_id, hashlib.sha256(b"report").hexdigest(), "FULL_TEXT", "2026-03-02"),
            ).lastrowid
            run_id = connection.execute(
                "INSERT INTO extraction_runs(document_version_id,model,prompt_version,schema_version,status,response_json) VALUES (?,'gpt-5.4','v1','1','SUCCEEDED','{}')", (version_id,)
            ).lastrowid
            chunk_id = connection.execute(
                "INSERT INTO extraction_chunks(extraction_run_id,chunk_index,start_offset,end_offset,content_hash,status,response_json) VALUES (?,0,0,6,?,'SUCCEEDED',?)",
                (run_id, hashlib.sha256(b"report").hexdigest(), json.dumps(payload)),
            ).lastrowid
            for local_id, entity_id in zip(("e1", "e2"), ids):
                connection.execute(
                    "INSERT INTO extracted_entity_candidates(extraction_chunk_id,local_id,candidate_json,resolved_entity_id,resolution_status,resolution_method,resolution_confidence) VALUES (?,?,?,?,?,?,?)",
                    (chunk_id, local_id, "{}", entity_id, "RESOLVED", "TEST", 1.0),
                )
            result = EvidenceMaterializer(connection).materialize(run_id)
            self.assertEqual(result, {"assertions": 2, "risk_events": 1})
            relation = connection.execute("SELECT * FROM assertions WHERE classification='RELATED_PARTY'").fetchone()
            self.assertEqual((relation["relationship_confidence"], relation["validation_status"]), ("HIGH", "VALIDATED"))
            self.assertEqual((relation["event_date"], relation["valid_from"], relation["published_at"], relation["retrieved_at"]),
                             ("2026-01-02", "2026-01-01", "2026-03-01", "2026-03-02"))
            evidence = connection.execute("SELECT evidence_text FROM evidence WHERE assertion_id=?", (relation["id"],)).fetchone()[0]
            self.assertEqual(evidence, "Alpha plc directly controls Beta Ltd.")
            risk = connection.execute("SELECT risk_severity FROM risk_events").fetchone()[0]
            self.assertEqual(risk, "MEDIUM")
            EvidenceMaterializer(connection).materialize(run_id)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM assertions").fetchone()[0], 2)

            # If two textual candidates later resolve to the same canonical
            # entity, their candidate relationship is not materialized as a
            # misleading business self-loop. The risk event remains valid.
            connection.execute(
                "UPDATE extracted_entity_candidates SET resolved_entity_id=? "
                "WHERE extraction_chunk_id=? AND local_id='e2'",
                (ids[0], chunk_id),
            )
            result = EvidenceMaterializer(connection).materialize(run_id)
            self.assertEqual(result, {"assertions": 1, "risk_events": 1})
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM assertions WHERE object_entity_id IS NOT NULL"
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
