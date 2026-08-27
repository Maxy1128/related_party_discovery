"""Download, parse, and conservatively match public risk lists."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup

from rpd.config import Settings
from rpd.http import PublicHttpClient
from rpd.normalize import normalize_name


@dataclass(frozen=True)
class WatchlistRecord:
    list_name: str
    record_id: str
    primary_name: str
    aliases: tuple[str, ...] = ()
    entity_type: str | None = None
    countries: tuple[str, ...] = ()
    addresses: tuple[str, ...] = ()
    identifiers: tuple[tuple[str, str], ...] = ()
    source_url: str = ""
    details: str | None = None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(name for name in (self.primary_name, *self.aliases) if name))


@dataclass(frozen=True)
class WatchlistSnapshot:
    list_name: str
    records: tuple[WatchlistRecord, ...]
    retrieved_at: str
    raw_path: Path
    source_url: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _local_name(child.tag) == name and child.text:
            return child.text.strip()
    return ""


def _descendants(element: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in element.iter() if _local_name(item.tag) == name]


def _person_or_org_name(element: ET.Element) -> str:
    parts = [_child_text(element, "firstName"), _child_text(element, "lastName")]
    return " ".join(part for part in parts if part).strip()


def parse_ofac_xml(content: bytes, source_url: str) -> tuple[WatchlistRecord, ...]:
    root = ET.fromstring(content)
    records: list[WatchlistRecord] = []
    for entry in _descendants(root, "sdnEntry"):
        record_id = _child_text(entry, "uid")
        name = _person_or_org_name(entry)
        if not record_id or not name:
            continue
        aliases = tuple(
            alias_name for alias in _descendants(entry, "aka")
            if (alias_name := _person_or_org_name(alias)) and normalize_name(alias_name) != normalize_name(name)
        )
        countries: list[str] = []
        addresses: list[str] = []
        for address in _descendants(entry, "address"):
            country = _child_text(address, "country")
            if country:
                countries.append(country)
            address_text = ", ".join(
                value for key in ("address1", "address2", "address3", "city", "stateOrProvince", "postalCode", "country")
                if (value := _child_text(address, key))
            )
            if address_text:
                addresses.append(address_text)
        identifiers = []
        for identifier in _descendants(entry, "id"):
            id_type = _child_text(identifier, "idType")
            id_number = _child_text(identifier, "idNumber")
            if id_type and id_number:
                identifiers.append((id_type, id_number))
        records.append(WatchlistRecord(
            list_name="OFAC", record_id=record_id, primary_name=name,
            aliases=tuple(dict.fromkeys(aliases)), entity_type=_child_text(entry, "sdnType") or None,
            countries=tuple(dict.fromkeys(countries)), addresses=tuple(dict.fromkeys(addresses)),
            identifiers=tuple(identifiers), source_url=source_url,
        ))
    return tuple(records)


def _row_value(row: dict[str, str], *candidates: str) -> str:
    normalized = {re.sub(r"[^a-z0-9]", "", key.casefold()): (value or "").strip() for key, value in row.items() if key}
    for candidate in candidates:
        value = normalized.get(re.sub(r"[^a-z0-9]", "", candidate.casefold()), "")
        if value:
            return value
    return ""


def parse_uk_csv(content: bytes, source_url: str) -> tuple[WatchlistRecord, ...]:
    text = content.decode("utf-8-sig", errors="replace")
    grouped: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text)):
        record_id = _row_value(row, "Unique ID", "UniqueID", "Group ID")
        name_parts = [_row_value(row, f"Name {index}") for index in range(1, 7)]
        name = " ".join(part for part in name_parts if part) or _row_value(row, "Name", "Full Name")
        if not record_id or not name:
            continue
        item = grouped.setdefault(record_id, {
            "names": [], "entity_type": None, "countries": [], "addresses": [], "details": [],
        })
        item["names"].append(name)
        non_latin = _row_value(row, "Name (non-Latin script)", "Name non Latin script")
        if non_latin:
            item["names"].append(non_latin)
        item["entity_type"] = item["entity_type"] or _row_value(row, "Entity Type", "Individual, Entity, Ship") or None
        country = _row_value(row, "Country", "Country of Birth", "Address Country")
        if country:
            item["countries"].append(country)
        address = _row_value(row, "Address", "Address 1")
        if address:
            item["addresses"].append(address)
        regime = _row_value(row, "Regime Name", "Regime")
        if regime:
            item["details"].append(regime)
    return tuple(
        WatchlistRecord(
            list_name="UK_SANCTIONS", record_id=record_id,
            primary_name=data["names"][0], aliases=tuple(dict.fromkeys(data["names"][1:])),
            entity_type=data["entity_type"], countries=tuple(dict.fromkeys(data["countries"])),
            addresses=tuple(dict.fromkeys(data["addresses"])), source_url=source_url,
            details="; ".join(dict.fromkeys(data["details"])) or None,
        )
        for record_id, data in grouped.items()
    )


def parse_world_bank_html(content: bytes, source_url: str) -> tuple[WatchlistRecord, ...]:
    soup = BeautifulSoup(content, "html.parser")
    records: list[WatchlistRecord] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["th", "td"])]
        header_keys = [normalize_name(value) for value in headers]
        name_index = next((i for i, value in enumerate(header_keys) if "name" in value and ("firm" in value or "individual" in value)), None)
        if name_index is None:
            continue
        country_index = next((i for i, value in enumerate(header_keys) if "country" in value), None)
        address_index = next((i for i, value in enumerate(header_keys) if "address" in value), None)
        grounds_index = next((i for i, value in enumerate(header_keys) if "ground" in value), None)
        for row in rows[1:]:
            values = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            if name_index >= len(values) or not values[name_index]:
                continue
            name = values[name_index]
            stable = "|".join(values)
            record_id = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
            country = values[country_index] if country_index is not None and country_index < len(values) else ""
            address = values[address_index] if address_index is not None and address_index < len(values) else ""
            details = values[grounds_index] if grounds_index is not None and grounds_index < len(values) else None
            records.append(WatchlistRecord(
                list_name="WORLD_BANK", record_id=record_id, primary_name=name,
                countries=(country,) if country else (), addresses=(address,) if address else (),
                source_url=source_url, details=details,
            ))
    return tuple(records)


class WatchlistClient:
    URLS = {
        "OFAC": ("ofac_sdn_xml_url", "xml", parse_ofac_xml),
        "UK_SANCTIONS": ("uk_sanctions_csv_url", "csv", parse_uk_csv),
        "WORLD_BANK": ("world_bank_debarred_url", "html", parse_world_bank_html),
    }

    def __init__(self, settings: Settings, http: PublicHttpClient | None = None):
        self.settings = settings
        self.http = http or PublicHttpClient(settings)

    def fetch(self, list_name: str) -> WatchlistSnapshot:
        key = list_name.upper()
        if key not in self.URLS:
            raise ValueError(f"Unsupported watchlist: {list_name}")
        setting_name, extension, parser = self.URLS[key]
        download = self.http.download(getattr(self.settings, setting_name))
        digest = hashlib.sha256(download.content).hexdigest()
        directory = self.settings.paths.raw / "watchlists" / key.casefold()
        directory.mkdir(parents=True, exist_ok=True)
        raw_path = directory / f"{digest}.{extension}"
        if not raw_path.exists():
            raw_path.write_bytes(download.content)
        records = parser(download.content, download.final_url)
        return WatchlistSnapshot(key, records, download.retrieved_at, raw_path, download.final_url)


class WatchlistMatcher:
    """Produce leads; confirmation requires name plus independent identity evidence."""

    def __init__(self, connection: sqlite3.Connection, fuzzy_threshold: float = 0.88):
        self.connection = connection
        self.fuzzy_threshold = fuzzy_threshold

    def match(self, entity_id: int, snapshot: WatchlistSnapshot) -> list[int]:
        entity = self.connection.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if not entity:
            raise ValueError(f"Unknown entity id: {entity_id}")
        entity_names = [entity["canonical_name"]]
        entity_names.extend(row[0] for row in self.connection.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id=?", (entity_id,)
        ))
        identifiers = {
            (normalize_name(row[0]), normalize_name(row[1]))
            for row in self.connection.execute(
                "SELECT identifier_scheme,identifier_value FROM entity_identifiers WHERE entity_id=?", (entity_id,)
            )
        }
        inserted: list[int] = []
        for record in snapshot.records:
            result = self._score(entity_names, entity["country_code"], entity["registered_address"], identifiers, record)
            if result is None:
                continue
            matched_name, method, score, status, rationale = result
            cursor = self.connection.execute(
                """INSERT INTO watchlist_matches(entity_id,list_name,list_record_id,matched_name,
                   match_method,match_score,match_status,rationale,source_url,source_retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(entity_id,list_name,list_record_id)
                   DO UPDATE SET matched_name=excluded.matched_name,match_method=excluded.match_method,
                     match_score=excluded.match_score,match_status=excluded.match_status,
                     rationale=excluded.rationale,source_url=excluded.source_url,
                     source_retrieved_at=excluded.source_retrieved_at""",
                (entity_id, snapshot.list_name, record.record_id, matched_name, method, score,
                 status, rationale, record.source_url or snapshot.source_url, snapshot.retrieved_at),
            )
            match_id = cursor.lastrowid
            if not match_id:
                match_id = self.connection.execute(
                    "SELECT id FROM watchlist_matches WHERE entity_id=? AND list_name=? AND list_record_id=?",
                    (entity_id, snapshot.list_name, record.record_id),
                ).fetchone()[0]
            inserted.append(int(match_id))
        self.connection.commit()
        return inserted

    def _score(self, entity_names: list[str], country_code: str | None, address: str | None,
               identifiers: set[tuple[str, str]], record: WatchlistRecord):
        pairs = [(entity_name, record_name, SequenceMatcher(None, normalize_name(entity_name), normalize_name(record_name)).ratio())
                 for entity_name in entity_names for record_name in record.names]
        entity_name, record_name, score = max(pairs, key=lambda item: item[2])
        exact = normalize_name(entity_name) == normalize_name(record_name)
        record_identifiers = {(normalize_name(scheme), normalize_name(value)) for scheme, value in record.identifiers}
        identifier_match = bool(identifiers & record_identifiers)
        country_match = bool(country_code) and any(
            normalize_name(country_code) == normalize_name(country) for country in record.countries
        )
        address_match = bool(address) and any(
            normalize_name(address) == normalize_name(record_address) for record_address in record.addresses
        )
        if exact and (identifier_match or country_match or address_match):
            corroborators = [name for name, present in (("identifier", identifier_match), ("country", country_match), ("address", address_match)) if present]
            return record_name, "EXACT_NAME_PLUS_IDENTITY", 1.0, "CONFIRMED", "Exact normalized name plus matching " + ", ".join(corroborators) + "."
        if exact:
            return record_name, "EXACT_NAME_ONLY", 1.0, "POTENTIAL", "Exact normalized name without independent identity corroboration."
        if score >= self.fuzzy_threshold:
            return record_name, "FUZZY_NAME_ONLY", score, "POTENTIAL", "Similar name without sufficient evidence for confirmation."
        return None
