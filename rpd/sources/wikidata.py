"""Wikidata aliases and auxiliary-identifier adapter."""

from __future__ import annotations

from typing import Any

from rpd.config import Settings
from rpd.http import PublicHttpClient
from rpd.models import IdentityProfile


PROPERTY_SCHEMES = {
    "P1278": "LEI",
    "P249": "TICKER",
    "P946": "ISIN",
}


class WikidataClient:
    def __init__(self, settings: Settings, http: PublicHttpClient | None = None):
        self.settings = settings
        self.http = http or PublicHttpClient(settings)

    def search(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        payload = self.http.get_json(
            self.settings.wikidata_api_url,
            params={
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "uselang": "en",
                "type": "item",
                "limit": max(1, min(limit, 50)),
                "format": "json",
            },
        )
        return [
            {
                "id": item.get("id", ""),
                "label": item.get("label", ""),
                "description": item.get("description", ""),
            }
            for item in payload.get("search", [])
        ]

    def get(self, entity_id: str) -> IdentityProfile:
        payload = self.http.get_json(
            self.settings.wikidata_api_url,
            params={
                "action": "wbgetentities",
                "ids": entity_id,
                "props": "labels|aliases|claims|sitelinks",
                "languages": "en",
                "format": "json",
            },
        )
        entity = payload.get("entities", {}).get(entity_id)
        if not entity or "missing" in entity:
            raise LookupError(f"Wikidata entity not found: {entity_id}")
        label = entity.get("labels", {}).get("en", {}).get("value") or entity_id
        aliases = tuple(
            item["value"]
            for item in entity.get("aliases", {}).get("en", [])
            if item.get("value") and item["value"].casefold() != label.casefold()
        )
        identifiers: list[tuple[str, str]] = [("WIKIDATA", entity_id)]
        for property_id, scheme in PROPERTY_SCHEMES.items():
            for value in self._claim_values(entity, property_id):
                if isinstance(value, str):
                    identifiers.append((scheme, value))
        websites = [
            value for value in self._claim_values(entity, "P856") if isinstance(value, str)
        ]
        leis = [value for scheme, value in identifiers if scheme == "LEI"]
        return IdentityProfile(
            canonical_name=label,
            # Wikidata is deliberately non-authoritative for legal identity.
            legal_name=None,
            lei=leis[0] if leis else None,
            website=websites[0] if websites else None,
            aliases=aliases,
            identifiers=tuple(identifiers),
            source="WIKIDATA",
            source_url=f"https://www.wikidata.org/wiki/{entity_id}",
            raw=entity,
        )

    @staticmethod
    def _claim_values(entity: dict[str, Any], property_id: str) -> list[Any]:
        values: list[Any] = []
        for claim in entity.get("claims", {}).get(property_id, []):
            snak = claim.get("mainsnak", {})
            if snak.get("snaktype") != "value":
                continue
            value = snak.get("datavalue", {}).get("value")
            if value is not None:
                values.append(value)
        return values
