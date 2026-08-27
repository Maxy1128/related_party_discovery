"""Conservative Mention-to-Entity normalization for extracted candidates."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from rpd.config import Settings
from rpd.extraction_schema import EntityCandidate, ExtractionPayload
from rpd.normalize import normalize_name

STRONG_IDENTIFIER_SCHEMES = {"LEI", "REGISTRATION_NUMBER", "COMPANY_NUMBER"}


@dataclass(frozen=True)
class ResolutionDecision:
    entity_id: int
    status: str
    method: str
    confidence: float
    details: dict


class EntityResolver:
    def __init__(self, settings: Settings, connection: sqlite3.Connection):
        self.settings = settings
        self.connection = connection

    def resolve_extraction_run(self, extraction_run_id: int) -> dict:
        run = self.connection.execute(
            """SELECT er.id,er.status,v.normalized_path FROM extraction_runs er
               JOIN document_versions v ON v.id=er.document_version_id WHERE er.id=?""",
            (extraction_run_id,),
        ).fetchone()
        if not run:
            raise LookupError(f"Extraction run not found: {extraction_run_id}")
        if run["status"] != "SUCCEEDED":
            raise ValueError("Only SUCCEEDED extraction runs can be resolved.")
        document_text = (self.settings.paths.root / run["normalized_path"]).read_text(
            encoding="utf-8"
        )
        chunks = self.connection.execute(
            """SELECT id,chunk_index,start_offset,end_offset,response_json FROM extraction_chunks
               WHERE extraction_run_id=? AND status='SUCCEEDED' ORDER BY chunk_index""",
            (extraction_run_id,),
        ).fetchall()
        self.connection.execute(
            "DELETE FROM mentions WHERE extraction_run_id=?", (extraction_run_id,)
        )
        self.connection.execute(
            """DELETE FROM extracted_entity_candidates WHERE extraction_chunk_id IN
               (SELECT id FROM extraction_chunks WHERE extraction_run_id=?)""",
            (extraction_run_id,),
        )
        candidate_count = 0
        mention_count = 0
        for chunk in chunks:
            payload = ExtractionPayload.model_validate_json(chunk["response_json"])
            decisions: dict[str, ResolutionDecision] = {}
            for candidate in payload.entities:
                decision = self.resolve_candidate(candidate)
                decisions[candidate.local_id] = decision
                self.connection.execute(
                    """INSERT INTO extracted_entity_candidates(
                       extraction_chunk_id,local_id,candidate_json,resolved_entity_id,
                       resolution_status,resolution_method,resolution_confidence,
                       resolution_details_json) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        chunk["id"], candidate.local_id,
                        candidate.model_dump_json(), decision.entity_id, decision.status,
                        decision.method, decision.confidence,
                        json.dumps(decision.details, sort_keys=True),
                    ),
                )
                candidate_count += 1
            for mention in payload.mentions:
                decision = decisions[mention.entity_local_id]
                relative_start = self._mention_offset(
                    mention.mention_text,
                    mention.evidence_text,
                    document_text[int(chunk["start_offset"]):int(chunk["end_offset"])],
                )
                start_offset = (
                    int(chunk["start_offset"]) + relative_start
                    if relative_start is not None
                    else None
                )
                end_offset = (
                    start_offset + len(mention.mention_text)
                    if start_offset is not None
                    else None
                )
                self.connection.execute(
                    """INSERT INTO mentions(
                       extraction_run_id,extraction_chunk_id,candidate_local_id,entity_id,
                       mention_text,normalized_mention,context_text,start_offset,end_offset,
                       resolution_status,resolution_confidence,resolution_method,
                       resolution_details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        extraction_run_id, chunk["id"], mention.entity_local_id,
                        decision.entity_id, mention.mention_text,
                        normalize_name(mention.mention_text), mention.context_text,
                        start_offset, end_offset, decision.status, decision.confidence,
                        decision.method, json.dumps(decision.details, sort_keys=True),
                    ),
                )
                mention_count += 1
        self.connection.commit()
        return {"candidates": candidate_count, "mentions": mention_count}

    def resolve_candidate(self, candidate: EntityCandidate) -> ResolutionDecision:
        identifier_matches = self._identifier_matches(candidate)
        if len(identifier_matches) == 1:
            return ResolutionDecision(
                identifier_matches[0], "RESOLVED", "IDENTIFIER_EXACT", 1.0,
                {"matched_identifiers": [item.model_dump() for item in candidate.identifiers]},
            )
        if len(identifier_matches) > 1:
            ambiguous_candidate = candidate.model_copy(update={"ambiguous": True})
            decision = self._create_or_reuse(ambiguous_candidate)
            return ResolutionDecision(
                decision.entity_id, decision.status, "IDENTIFIER_CONFLICT",
                0.25, {"matched_entity_ids": identifier_matches},
            )
        exact_matches = self._name_matches(candidate)
        if len(exact_matches) == 1:
            return ResolutionDecision(
                exact_matches[0], "RESOLVED", "NAME_EXACT", 0.98,
                {"normalized_name": normalize_name(candidate.name)},
            )
        if len(exact_matches) > 1:
            selected_alias = self._investigation_alias_match(candidate)
            if selected_alias is not None and selected_alias in exact_matches:
                return ResolutionDecision(
                    selected_alias, "RESOLVED", "INVESTIGATION_ALIAS", 0.99,
                    {"normalized_name": normalize_name(candidate.name)},
                )
        fuzzy = self._fuzzy_matches(candidate)
        if fuzzy:
            best_id, best_score, second_score = fuzzy
            if (
                not candidate.ambiguous
                and best_score >= self.settings.entity_fuzzy_threshold
                and best_score - second_score >= self.settings.entity_fuzzy_margin
            ):
                return ResolutionDecision(
                    best_id, "RESOLVED", "FUZZY_HIGH", best_score,
                    {"best_score": best_score, "second_score": second_score},
                )
        return self._create_or_reuse(candidate)

    def _identifier_matches(self, candidate: EntityCandidate) -> list[int]:
        matches = set()
        for identifier in candidate.identifiers:
            rows = self.connection.execute(
                """SELECT entity_id FROM entity_identifiers
                   WHERE upper(identifier_scheme)=upper(?) AND upper(identifier_value)=upper(?)""",
                (identifier.scheme, identifier.value),
            ).fetchall()
            matches.update(int(row["entity_id"]) for row in rows)
            if identifier.scheme.upper() == "LEI":
                rows = self.connection.execute(
                    "SELECT id FROM entities WHERE upper(lei)=upper(?)", (identifier.value,)
                ).fetchall()
                matches.update(int(row["id"]) for row in rows)
        return sorted(matches)

    def _name_matches(self, candidate: EntityCandidate) -> list[int]:
        names = {normalize_name(candidate.name), *(normalize_name(x) for x in candidate.aliases)}
        names.discard("")
        if not names:
            return []
        placeholders = ",".join("?" for _ in names)
        rows = self.connection.execute(
            f"""SELECT id AS entity_id FROM entities WHERE normalized_name IN ({placeholders})
                UNION SELECT entity_id FROM entity_aliases WHERE normalized_alias IN ({placeholders})""",
            (*names, *names),
        ).fetchall()
        return sorted({int(row["entity_id"]) for row in rows})

    def _investigation_alias_match(self, candidate: EntityCandidate) -> int | None:
        names = {
            normalize_name(candidate.name),
            *(normalize_name(alias) for alias in candidate.aliases),
        }
        names.discard("")
        if not names:
            return None
        placeholders = ",".join("?" for _ in names)
        rows = self.connection.execute(
            f"""SELECT DISTINCT entity_id FROM entity_aliases
                 WHERE normalized_alias IN ({placeholders})
                   AND source='INVESTIGATION_QUERY'""",
            tuple(names),
        ).fetchall()
        return int(rows[0]["entity_id"]) if len(rows) == 1 else None

    def _fuzzy_matches(self, candidate: EntityCandidate):
        rows = self.connection.execute(
            "SELECT id,normalized_name,country_code FROM entities ORDER BY id"
        ).fetchall()
        if not rows:
            return None
        query = normalize_name(candidate.name)
        labels = [row["normalized_name"] for row in rows]
        if not query or not any(labels):
            return None
        matrix = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit_transform(
            [query, *labels]
        )
        scores = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
        adjusted = []
        for row, raw_score in zip(rows, scores):
            score = float(raw_score)
            if candidate.country_code and row["country_code"]:
                score += 0.03 if candidate.country_code == row["country_code"] else -0.03
            adjusted.append((int(row["id"]), max(0.0, min(1.0, score))))
        adjusted.sort(key=lambda item: item[1], reverse=True)
        second = adjusted[1][1] if len(adjusted) > 1 else 0.0
        return adjusted[0][0], adjusted[0][1], second

    def _create_or_reuse(self, candidate: EntityCandidate) -> ResolutionDecision:
        normalized = normalize_name(candidate.name)
        scope = candidate.entity_scope
        identified = (
            any(
                item.scheme.upper() in STRONG_IDENTIFIER_SCHEMES
                for item in candidate.identifiers
            )
            and not candidate.ambiguous
        )
        if candidate.entity_type == "ORGANIZATION" and not identified:
            scope = "GROUP"
        ambiguous = candidate.ambiguous or not identified
        row = self.connection.execute(
            """SELECT id FROM entities WHERE normalized_name=? AND entity_scope=?
               AND ambiguous=? AND country_code IS ? ORDER BY id LIMIT 1""",
            (normalized, scope, int(ambiguous), candidate.country_code),
        ).fetchone()
        if row:
            entity_id = int(row["id"])
            method = "AMBIGUOUS_ENTITY_REUSE" if ambiguous else "IDENTIFIED_ENTITY_REUSE"
        else:
            lei = next(
                (x.value for x in candidate.identifiers if x.scheme.upper() == "LEI"), None
            )
            entity_id = int(self.connection.execute(
                """INSERT INTO entities(
                   canonical_name,normalized_name,legal_name,entity_scope,entity_type,
                   lei,country_code,ambiguous) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    candidate.name, normalized, candidate.name if identified else None,
                    scope, candidate.entity_type, lei, candidate.country_code, int(ambiguous),
                ),
            ).lastrowid)
            method = "NEW_AMBIGUOUS_GROUP" if ambiguous else "NEW_IDENTIFIED_ENTITY"
        for alias in candidate.aliases:
            self.connection.execute(
                """INSERT OR IGNORE INTO entity_aliases(entity_id,alias,normalized_alias,source)
                   VALUES (?,?,?,'LLM_CANDIDATE')""",
                (entity_id, alias, normalize_name(alias)),
            )
        for identifier in candidate.identifiers:
            self.connection.execute(
                """INSERT OR IGNORE INTO entity_identifiers(
                   entity_id,identifier_scheme,identifier_value,source)
                   VALUES (?,?,?,'LLM_CANDIDATE')""",
                (entity_id, identifier.scheme, identifier.value),
            )
        status = "AMBIGUOUS" if ambiguous else "RESOLVED"
        if scope == "GROUP" and ambiguous:
            status = "GROUP_LEVEL"
        return ResolutionDecision(entity_id, status, method, 0.55 if ambiguous else 0.9, {})

    @staticmethod
    def _mention_offset(
        mention_text: str, evidence_text: str, chunk_text: str
    ) -> int | None:
        evidence_index = chunk_text.find(evidence_text)
        mention_index = evidence_text.find(mention_text)
        if evidence_index < 0 or mention_index < 0:
            return None
        return evidence_index + mention_index
