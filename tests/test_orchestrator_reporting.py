from __future__ import annotations

import hashlib
import tempfile
import unittest
from unittest.mock import patch

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.models import IdentityProfile
from rpd.orchestrator import InvestigationOrchestrator, InvestigationRequest
from rpd.reporting import BOUNDARY_NOTICE, ReportBuilder


class OrchestratorAndReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings.from_env({"RPD_DATA_DIR": self.temp.name})
        self.settings.paths.create()
        initialize(self.settings.paths.sqlite_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_generic_run_records_every_step_without_case_specific_logic(self):
        profile = IdentityProfile(
            canonical_name="Example Holdings plc", legal_name="Example Holdings plc",
            lei="TESTLEI00000000000001", country_code="GB", source="GLEIF",
            source_url="https://gleif.example/lei/TEST", identifiers=(("LEI", "TESTLEI00000000000001"),),
        )
        with connect(self.settings.paths.sqlite_path) as connection:
            orchestrator = InvestigationOrchestrator(self.settings, connection)
            with patch("rpd.orchestrator.GleifClient.get", return_value=profile), patch.object(
                orchestrator, "_enrich_wikidata", return_value=None
            ):
                investigation_id = orchestrator.run(InvestigationRequest(
                    company_query="Example Holdings", selected_lei=profile.lei,
                    include_news=False, include_watchlists=False,
                ))
            investigation = connection.execute(
                "SELECT status,target_entity_id,parameters_json FROM investigations WHERE id=?", (investigation_id,)
            ).fetchone()
            self.assertEqual(investigation["status"], "COMPLETED")
            self.assertIsNotNone(investigation["target_entity_id"])
            steps = connection.execute(
                "SELECT step_name,status FROM investigation_steps WHERE investigation_id=? ORDER BY id", (investigation_id,)
            ).fetchall()
            self.assertEqual(len(steps), 7)
            self.assertEqual(dict(steps)["Identity"], "COMPLETED")
            self.assertEqual(dict(steps)["News"], "SKIPPED")
            self.assertEqual(dict(steps)["Descriptions"], "SKIPPED")
            self.assertEqual(dict(steps)["Report ready"], "COMPLETED")

    def test_report_sections_timeline_and_limited_two_hop_graph(self):
        with connect(self.settings.paths.sqlite_path) as connection:
            target, first, risky = [connection.execute(
                "INSERT INTO entities(canonical_name,normalized_name,lei) VALUES (?,?,?)",
                (name, name.casefold(), lei),
            ).lastrowid for name, lei in (
                ("Target plc", "LEI-TARGET"), ("First Hop Ltd", "LEI-FIRST"), ("Risky Second Hop", "LEI-RISKY")
            )]
            investigation_id = connection.execute(
                "INSERT INTO investigations(target_entity_id,title,status,parameters_json) VALUES (?,'Target investigation','COMPLETED','{}')", (target,)
            ).lastrowid
            document_id = connection.execute(
                "INSERT INTO documents(source_type,title,publisher,original_url,normalized_url,published_at,first_retrieved_at) VALUES ('OFFICIAL_DOCUMENT','Disclosure','Target','https://example.test/disclosure','https://example.test/disclosure','2026-05-01','2026-05-02')"
            ).lastrowid
            version_id = connection.execute(
                "INSERT INTO document_versions(document_id,content_hash,retrieval_status,retrieved_at) VALUES (?,?,?,?)",
                (document_id, hashlib.sha256(b"disclosure").hexdigest(), "FULL_TEXT", "2026-05-02"),
            ).lastrowid
            connection.execute("INSERT INTO investigation_documents(investigation_id,document_id) VALUES (?,?)", (investigation_id, document_id))
            relation_type = connection.execute("SELECT id FROM relation_type_registry WHERE canonical_name='PARENT_OF'").fetchone()[0]
            relation_ids = []
            for subject, obj, text, date in (
                (target, first, "Target plc controls First Hop Ltd.", "2026-04-01"),
                (first, risky, "First Hop Ltd controls Risky Second Hop.", "2026-04-15"),
            ):
                assertion_id = connection.execute(
                    """INSERT INTO assertions(subject_entity_id,object_entity_id,relation_type_id,
                       normalized_relation_type,classification,assertion_text,explicit_or_inferred,
                       validation_status,relationship_confidence,event_date,published_at,retrieved_at)
                       VALUES (?,?,?,'PARENT_OF','RELATED_PARTY',?,'EXPLICIT','VALIDATED','HIGH',?,'2026-05-01','2026-05-02')""",
                    (subject, obj, relation_type, text, date),
                ).lastrowid
                relation_ids.append(assertion_id)
                connection.execute(
                    "INSERT INTO evidence(assertion_id,document_version_id,evidence_text) VALUES (?,?,?)",
                    (assertion_id, version_id, text),
                )
                connection.execute("INSERT INTO investigation_assertions(investigation_id,assertion_id) VALUES (?,?)", (investigation_id, assertion_id))
            connection.execute(
                """INSERT INTO watchlist_matches(entity_id,list_name,list_record_id,matched_name,
                   match_method,match_score,match_status,rationale,source_url,source_retrieved_at)
                   VALUES (?,'OFAC','R1','Risky Second Hop','EXACT_NAME_ONLY',1,'POTENTIAL',
                   'Name requires corroboration.','https://example.test/list','2026-05-03')""", (risky,)
            )
            connection.execute(
                """INSERT INTO entity_descriptions(investigation_id,entity_id,description,
                   generation_method,model,prompt_version,input_hash)
                   VALUES (?,?,'Target plc is the investigation target and controls First Hop Ltd.',
                   'LLM_GENERATED','test-model','test-prompt','entity-hash')""",
                (investigation_id, target),
            )
            for relation_id in relation_ids:
                connection.execute(
                    """INSERT INTO relationship_descriptions(investigation_id,assertion_id,
                       description,generation_method,model,prompt_version,input_hash)
                       VALUES (?,?,'This relationship is summarized from the supporting disclosure.',
                       'LLM_GENERATED','test-model','test-prompt',?)""",
                    (investigation_id, relation_id, f"relation-hash-{relation_id}"),
                )
            view = ReportBuilder(connection).load(investigation_id)
            graph = ReportBuilder(connection).graph(view)
            self.assertEqual(set(graph.nodes), {target, first, risky})
            self.assertEqual(len(graph.edges), 2)
            self.assertEqual(graph.nodes[target]["description"], view.profile["entity_description"])
            self.assertEqual([item["timeline_date"] for item in view.timeline], ["2026-04-01", "2026-04-15"])
            markdown = ReportBuilder(connection).markdown(view)
            self.assertIn("Validated Related Parties", markdown)
            self.assertIn(BOUNDARY_NOTICE, markdown)
            self.assertIn("Investigation status: COMPLETED", markdown)
            self.assertIn("AI-generated summary", markdown)
            self.assertIn("https://example.test/disclosure", markdown)
            html = ReportBuilder(connection).html(view)
            self.assertIn("<!doctype html>", html)
            self.assertIn("Data Limitations", html)
            self.assertIn('href="https://example.test/disclosure"', html)
            self.assertIn('href="https://example.test/list"', html)


if __name__ == "__main__":
    unittest.main()
