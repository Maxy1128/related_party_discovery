"""Reusable Rio Tinto document set and deterministic smoke-test checks."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from rpd.config import Settings
from rpd.extraction import chunk_text
from rpd.models import OfficialSource
from rpd.normalize import normalize_url
from rpd.sources.official import OfficialDocumentIngestor, RIO_TINTO_OFFICIAL_SOURCES


@dataclass(frozen=True)
class SmokeDocument:
    source: OfficialSource
    evidence_needles: tuple[str, ...]
    ingest_if_missing: bool = True


RIO_TINTO_SMOKE_DOCUMENTS = (
    SmokeDocument(RIO_TINTO_OFFICIAL_SOURCES[0], ("Annual report", "Related party"), False),
    SmokeDocument(RIO_TINTO_OFFICIAL_SOURCES[1], ("Rio Tinto plc",), False),
    SmokeDocument(RIO_TINTO_OFFICIAL_SOURCES[2], ("transparency",), False),
    SmokeDocument(OfficialSource(
        key="annual_report_2025", title="Rio Tinto Annual Report 2025",
        url=("https://cdn-rio.dataweavers.io/-/media/content/documents/invest/reports/"
             "annual-reports/2025-annual-report.pdf?rev=928756ce35df4757be31105d2665bd55"),
        publisher="Rio Tinto", published_at="2026-02-19",
    ), ("Annual report 2025", "Related party")),
    SmokeDocument(OfficialSource(
        key="dampier_joint_venture_2026",
        title="Rio Tinto and WA Government partner to expand Dampier Seawater Desalination Plant",
        url="https://www.riotinto.com/en/news/releases/2026/rio-tinto-and-wa-government-partner-to-expand-dampier-seawater-desalination-plant",
        publisher="Rio Tinto", published_at="2026-03-04",
    ), ("50:50 joint venture", "Western Australian Government")),
    SmokeDocument(OfficialSource(
        key="jinbi_ppa_2026",
        title="Yindjibarndi Energy Corporation signs Power Purchase Agreement with Rio Tinto",
        url="https://www.riotinto.com/en/news/releases/2026/yindjibarndi-energy-reaches-financial-close-jinbi-solar-project-power-purchase-agreement-rio-tinto",
        publisher="Rio Tinto", published_at="2026-05-11",
    ), ("Power Purchase Agreement", "Yindjibarndi Energy")),
    SmokeDocument(OfficialSource(
        key="boyne_partnership_2026",
        title="Rio Tinto, Queensland and Commonwealth secure long-term future for Boyne aluminium smelter",
        url="https://www.riotinto.com/news/releases/2026/rio-tinto-queensland-and-commonwealth-secure-long-term-future-for-boyne-aluminium-smelter-at-gladstone",
        publisher="Rio Tinto", published_at="2026-03-25",
    ), ("Queensland Government", "Boyne Smelters Limited")),
    SmokeDocument(OfficialSource(
        key="nemaska_majority_interest_2026",
        title="Rio Tinto assumes majority interest and management responsibilities at Nemaska Lithium",
        url="https://www.riotinto.com/en/can/news/releases/2026/rio-tinto-assumes-majority-interest-and-management-responsibilities-at-nemaska-lithium",
        publisher="Rio Tinto", published_at="2026-02-18",
    ), ("53.9% stake", "Nemaska Lithium")),
)


def ingest_missing_smoke_documents(
    settings: Settings, connection: sqlite3.Connection
) -> list[dict]:
    ingestor = OfficialDocumentIngestor(settings, connection)
    outcomes = []
    for fixture in RIO_TINTO_SMOKE_DOCUMENTS:
        normalized = normalize_url(fixture.source.url)
        existing = connection.execute(
            """SELECT d.id document_id,v.id document_version_id,v.retrieval_status
               FROM documents d JOIN document_versions v ON v.document_id=d.id AND v.is_current=1
               WHERE d.source_type=? AND d.normalized_url=?""",
            (fixture.source.source_type, normalized),
        ).fetchone()
        if existing:
            outcomes.append({"key": fixture.source.key, **dict(existing), "downloaded": False})
            continue
        if not fixture.ingest_if_missing:
            outcomes.append({"key": fixture.source.key, "error": "Required retained document is missing."})
            continue
        try:
            result = ingestor.ingest(fixture.source)
            connection.commit()
            outcomes.append({
                "key": fixture.source.key, "document_id": result.document_id,
                "document_version_id": result.document_version_id,
                "retrieval_status": result.retrieval_status, "downloaded": True,
            })
        except Exception as exc:
            outcomes.append({"key": fixture.source.key, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
    return outcomes


def validate_smoke_documents(
    settings: Settings, connection: sqlite3.Connection
) -> list[dict]:
    results = []
    for fixture in RIO_TINTO_SMOKE_DOCUMENTS:
        row = connection.execute(
            """SELECT d.id document_id,d.original_url,v.id document_version_id,v.content_hash,
                      v.normalized_path,v.retrieval_status
               FROM documents d JOIN document_versions v ON v.document_id=d.id AND v.is_current=1
               WHERE d.source_type=? AND d.normalized_url=?""",
            (fixture.source.source_type, normalize_url(fixture.source.url)),
        ).fetchone()
        checks: dict[str, bool] = {"stored": bool(row)}
        character_count = chunk_count = 0
        matched_needles: list[str] = []
        if row and row["normalized_path"]:
            path = settings.paths.root / row["normalized_path"]
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            character_count = len(text)
            checks["full_text"] = row["retrieval_status"] == "FULL_TEXT" and character_count >= 500
            checks["content_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest() == row["content_hash"]
            matched_needles = [needle for needle in fixture.evidence_needles if needle.casefold() in text.casefold()]
            checks["expected_content"] = bool(matched_needles)
            chunks = chunk_text(text, settings.extraction_chunk_chars, settings.extraction_chunk_overlap_chars) if text else []
            chunk_count = len(chunks)
            checks["chunkable"] = bool(chunks) and all(chunk.content_hash for chunk in chunks)
        else:
            checks.update(full_text=False, content_hash=False, expected_content=False, chunkable=False)
        results.append({
            "key": fixture.source.key, "document_id": row["document_id"] if row else None,
            "document_version_id": row["document_version_id"] if row else None,
            "characters": character_count, "chunks": chunk_count,
            "matched_needles": matched_needles, "checks": checks, "passed": all(checks.values()),
        })
    return results
