"""Strict candidate-fact schema returned by the LLM."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateIdentifier(StrictModel):
    scheme: str
    value: str


class EntityCandidate(StrictModel):
    local_id: str
    name: str
    entity_type: Literal["ORGANIZATION", "PERSON", "GOVERNMENT", "OTHER"]
    entity_scope: Literal["LEGAL_ENTITY", "GROUP", "PERSON", "GOVERNMENT", "OTHER"]
    aliases: list[str]
    identifiers: list[CandidateIdentifier]
    country_code: str | None
    ambiguous: bool
    ambiguity_flags: list[str]


class EntityMentionCandidate(StrictModel):
    entity_local_id: str
    mention_text: str
    context_text: str
    evidence_text: str
    evidence_quality: Literal["EXACT", "PARAPHRASED"] = "EXACT"


class RelationshipCandidate(StrictModel):
    subject_local_id: str
    object_local_id: str
    subject_role: str
    object_role: str
    classification: Literal[
        "RELATED_PARTY", "COUNTERPARTY", "RISK_RELATION", "CO_MENTION"
    ]
    normalized_relation_type: str
    proposed_relation_type: str | None
    explicit_or_inferred: Literal["EXPLICIT", "INFERRED"]
    event_date: str | None
    valid_from: str | None
    valid_to: str | None
    evidence_text: str
    ambiguity_flags: list[str]
    evidence_quality: Literal["EXACT", "PARAPHRASED"] = "EXACT"


class RiskEventCandidate(StrictModel):
    entity_local_id: str
    event_type: str
    description: str
    event_date: str | None
    risk_severity_candidate: Literal[
        "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL", "UNKNOWN"
    ]
    evidence_text: str
    ambiguity_flags: list[str]
    evidence_quality: Literal["EXACT", "PARAPHRASED"] = "EXACT"


class ExtractionPayload(StrictModel):
    entities: list[EntityCandidate]
    mentions: list[EntityMentionCandidate]
    relationships: list[RelationshipCandidate]
    risk_events: list[RiskEventCandidate]
    document_date: str | None
    ambiguity_flags: list[str]
