"""Materialize candidate relations and apply deterministic evidence rules."""

from __future__ import annotations

import json
import sqlite3

from rpd.extraction_schema import ExtractionPayload
from rpd.registry import resolve_relation_type, seed_relation_registry


def relationship_confidence(
    source_type: str,
    explicit: str,
    classification: str,
    normalized_relation_type: str | None = None,
) -> str:
    if classification == "CO_MENTION" or explicit == "INFERRED":
        return "UNVERIFIED"
    if normalized_relation_type == "OTHER_MATERIAL_RELATION":
        return "LOW"
    if source_type == "OFFICIAL_DOCUMENT":
        return "HIGH"
    if source_type == "NEWS":
        return "LOW"
    return "LOW"


def risk_severity(event_type: str) -> str:
    value = event_type.casefold()
    if "sanction" in value or "debar" in value:
        return "CRITICAL"
    if any(term in value for term in ("fraud", "corrupt", "money laundering", "brib")):
        return "HIGH"
    if any(term in value for term in ("investigation", "lawsuit", "litigation", "human rights")):
        return "MEDIUM"
    if any(term in value for term in ("environment", "regulatory")):
        return "LOW"
    return "INFORMATIONAL"


class EvidenceMaterializer:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def materialize(self, extraction_run_id: int) -> dict:
        run = self.connection.execute(
            """SELECT er.id,er.status,v.id document_version_id,v.retrieved_at,
                      d.source_type,d.published_at,d.publisher
               FROM extraction_runs er JOIN document_versions v ON v.id=er.document_version_id
               JOIN documents d ON d.id=v.document_id WHERE er.id=?""",
            (extraction_run_id,),
        ).fetchone()
        if not run or run["status"] != "SUCCEEDED":
            raise ValueError("A successful extraction run is required.")
        seed_relation_registry(self.connection)
        old_ids = [row[0] for row in self.connection.execute(
            "SELECT id FROM assertions WHERE extraction_run_id=?", (extraction_run_id,)
        )]
        if old_ids:
            placeholders = ",".join("?" for _ in old_ids)
            self.connection.execute(f"DELETE FROM risk_events WHERE assertion_id IN ({placeholders})", old_ids)
        self.connection.execute("DELETE FROM assertions WHERE extraction_run_id=?", (extraction_run_id,))
        assertion_count = risk_count = 0
        chunks = self.connection.execute(
            "SELECT id,response_json FROM extraction_chunks WHERE extraction_run_id=? AND status='SUCCEEDED'",
            (extraction_run_id,),
        ).fetchall()
        for chunk in chunks:
            payload = ExtractionPayload.model_validate_json(chunk["response_json"])
            entity_map = {
                row["local_id"]: int(row["resolved_entity_id"])
                for row in self.connection.execute(
                    "SELECT local_id,resolved_entity_id FROM extracted_entity_candidates WHERE extraction_chunk_id=?",
                    (chunk["id"],),
                )
            }
            for relation in payload.relationships:
                subject_entity_id = entity_map[relation.subject_local_id]
                object_entity_id = entity_map[relation.object_local_id]
                # Distinct source mentions can resolve to the same canonical entity
                # (for example, a legal name and a short group alias). Such a pair
                # is not evidence of a relationship and must not become a self-loop.
                if subject_entity_id == object_entity_id:
                    continue
                registry_id, canonical = resolve_relation_type(
                    self.connection, relation.normalized_relation_type
                )
                confidence = relationship_confidence(
                    run["source_type"], relation.explicit_or_inferred,
                    relation.classification, canonical,
                )
                classification = self._governed_classification(
                    registry_id, canonical, relation.classification
                )
                assertion_id = self._insert_assertion(
                    extraction_run_id, subject_entity_id,
                    object_entity_id, registry_id, canonical,
                    relation.proposed_relation_type, classification,
                    relation.evidence_text, relation.explicit_or_inferred, confidence,
                    relation.event_date, relation.valid_from, relation.valid_to, run,
                    relation.ambiguity_flags,
                )
                self._insert_evidence(assertion_id, run["document_version_id"], relation.evidence_text, relation.evidence_quality)
                assertion_count += 1
            for event in payload.risk_events:
                registry_id, canonical = resolve_relation_type(self.connection, event.event_type)
                confidence = relationship_confidence(run["source_type"], "EXPLICIT", "RISK_RELATION")
                assertion_id = self._insert_assertion(
                    extraction_run_id, entity_map[event.entity_local_id], None, registry_id,
                    canonical, event.event_type, "RISK_RELATION", event.description,
                    "EXPLICIT", confidence, event.event_date, None, None, run,
                    event.ambiguity_flags,
                )
                self._insert_evidence(assertion_id, run["document_version_id"], event.evidence_text, event.evidence_quality)
                self.connection.execute(
                    """INSERT INTO risk_events(entity_id,assertion_id,event_type,description,
                       risk_severity,event_date,published_at,retrieved_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (entity_map[event.entity_local_id], assertion_id, event.event_type,
                     event.description, risk_severity(event.event_type), event.event_date,
                     run["published_at"], run["retrieved_at"]),
                )
                assertion_count += 1
                risk_count += 1
        self._apply_news_corroboration()
        self.connection.commit()
        return {"assertions": assertion_count, "risk_events": risk_count}

    def _governed_classification(
        self, registry_id: int, canonical: str, candidate: str
    ) -> str:
        if canonical == "CO_MENTION":
            return "CO_MENTION"
        family = self.connection.execute(
            "SELECT relation_family FROM relation_type_registry WHERE id=?",
            (registry_id,),
        ).fetchone()[0]
        if family in ("CORPORATE_STRUCTURE", "MANAGEMENT_OWNERSHIP"):
            return "RELATED_PARTY"
        if family == "COMMERCIAL":
            return "COUNTERPARTY"
        if family == "REGULATORY_RISK":
            return "RISK_RELATION"
        return candidate

    def _insert_assertion(self, run_id, subject, obj, registry_id, canonical, proposed,
                          classification, text, explicit, confidence, event_date,
                          valid_from, valid_to, run, flags):
        if "PARAPHRASED_EVIDENCE" in flags:
            confidence = "UNVERIFIED"
        status = "VALIDATED" if confidence in ("HIGH", "MEDIUM") else "CANDIDATE"
        return int(self.connection.execute(
            """INSERT INTO assertions(extraction_run_id,subject_entity_id,object_entity_id,
               relation_type_id,normalized_relation_type,proposed_relation_type,classification,
               assertion_text,explicit_or_inferred,validation_status,relationship_confidence,
               event_date,valid_from,valid_to,published_at,retrieved_at,ambiguity_flags_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,json(?))""",
            (run_id,subject,obj,registry_id,canonical,proposed,classification,text,explicit,
             status,confidence,event_date,valid_from,valid_to,run["published_at"],
             run["retrieved_at"],json.dumps(flags)),
        ).lastrowid)

    def _insert_evidence(self, assertion_id, version_id, text, quality="EXACT"):
        self.connection.execute(
            """INSERT INTO evidence(assertion_id,document_version_id,evidence_text,
               evidence_kind,evidence_quality,supports_assertion) VALUES (?,?,?,'FULL_TEXT',?,1)""",
            (assertion_id, version_id, text, quality),
        )

    def _apply_news_corroboration(self):
        groups = self.connection.execute(
            """SELECT a.subject_entity_id,a.object_entity_id,a.normalized_relation_type,
                      COUNT(DISTINCT COALESCE(d.publisher,d.normalized_url)) source_count
               FROM assertions a JOIN extraction_runs er ON er.id=a.extraction_run_id
               JOIN document_versions v ON v.id=er.document_version_id
               JOIN documents d ON d.id=v.document_id
               WHERE d.source_type='NEWS' AND a.explicit_or_inferred='EXPLICIT'
                 AND a.classification<>'CO_MENTION'
               GROUP BY a.subject_entity_id,a.object_entity_id,a.normalized_relation_type
               HAVING source_count>=2"""
        ).fetchall()
        for group in groups:
            self.connection.execute(
                """UPDATE assertions SET relationship_confidence='MEDIUM',validation_status='VALIDATED'
                   WHERE subject_entity_id=? AND object_entity_id IS ? AND normalized_relation_type=?
                     AND explicit_or_inferred='EXPLICIT' AND extraction_run_id IN (
                       SELECT er.id FROM extraction_runs er JOIN document_versions v ON v.id=er.document_version_id
                       JOIN documents d ON d.id=v.document_id WHERE d.source_type='NEWS')""",
                (group["subject_entity_id"], group["object_entity_id"], group["normalized_relation_type"]),
            )
