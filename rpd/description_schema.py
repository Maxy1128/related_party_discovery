"""Strict structured outputs for investigation-context descriptions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityDescriptionItem(StrictModel):
    entity_id: int
    description: str


class EntityDescriptionPayload(StrictModel):
    entities: list[EntityDescriptionItem]


class RelationshipDescriptionItem(StrictModel):
    assertion_id: int
    description: str


class RelationshipDescriptionPayload(StrictModel):
    relationships: list[RelationshipDescriptionItem]
