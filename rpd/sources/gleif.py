"""GLEIF identity and accounting-parent adapter."""

from __future__ import annotations

from typing import Any

from rpd.config import Settings
from rpd.http import PublicHttpClient
from rpd.models import IdentityProfile, ParentReference


class GleifClient:
    def __init__(self, settings: Settings, http: PublicHttpClient | None = None):
        self.settings = settings
        self.http = http or PublicHttpClient(settings)

    def search_legal_name(self, legal_name: str, limit: int = 10) -> list[IdentityProfile]:
        payload = self.http.get_json(
            f"{self.settings.gleif_api_base}/lei-records",
            params={
                "filter[entity.legalName]": legal_name,
                "page[size]": max(1, min(limit, 100)),
            },
        )
        return [self._profile(item, include_parents=False) for item in payload.get("data", [])]

    def get(self, lei: str, include_parents: bool = True) -> IdentityProfile:
        url = f"{self.settings.gleif_api_base}/lei-records/{lei}"
        payload = self.http.get_json(url)
        return self._profile(payload["data"], include_parents=include_parents)

    def _profile(self, record: dict[str, Any], include_parents: bool) -> IdentityProfile:
        attributes = record.get("attributes", {})
        entity = attributes.get("entity", {})
        legal_name = entity.get("legalName", {}).get("name") or record.get("id")
        legal_address = entity.get("legalAddress", {})
        address_parts = [
            *(legal_address.get("addressLines") or []),
            legal_address.get("city"),
            legal_address.get("region"),
            legal_address.get("postalCode"),
            legal_address.get("country"),
        ]
        aliases = tuple(
            item["name"]
            for item in entity.get("otherNames", [])
            if item.get("name") and item["name"].casefold() != legal_name.casefold()
        )
        parents: tuple[ParentReference, ...] = ()
        if include_parents:
            parents = tuple(
                self._parent_reference(record, relation)
                for relation in ("direct-parent", "ultimate-parent")
            )
        lei = attributes.get("lei") or record.get("id")
        return IdentityProfile(
            canonical_name=legal_name,
            legal_name=legal_name,
            lei=lei,
            registration_number=entity.get("registeredAs"),
            registration_authority=(entity.get("registeredAt") or {}).get("id"),
            country_code=entity.get("jurisdiction") or legal_address.get("country"),
            registered_address=", ".join(str(x) for x in address_parts if x),
            aliases=aliases,
            identifiers=(("LEI", lei),) if lei else (),
            parents=parents,
            source="GLEIF",
            source_url=record.get("links", {}).get("self", ""),
            raw=record,
        )

    def _parent_reference(self, record: dict[str, Any], relation: str) -> ParentReference:
        links = record.get("relationships", {}).get(relation, {}).get("links", {})
        related_url = links.get("related")
        if related_url:
            payload = self.http.get_json(related_url)
            data = payload.get("data") or {}
            parent_lei = data.get("id")
            if data.get("type") == "relationship-records":
                parent_lei = (
                    data.get("attributes", {})
                    .get("relationship", {})
                    .get("endNode", {})
                    .get("nodeId")
                )
            return ParentReference(
                relationship=relation,
                status="AVAILABLE",
                parent_lei=parent_lei,
                source_url=related_url,
            )
        exception_url = links.get("reporting-exception")
        reason = None
        if exception_url:
            payload = self.http.get_json(exception_url)
            reason = (payload.get("data") or {}).get("attributes", {}).get("reason")
        return ParentReference(
            relationship=relation,
            status="REPORTING_EXCEPTION" if exception_url else "NOT_AVAILABLE",
            exception_reason=reason,
            source_url=exception_url,
        )
