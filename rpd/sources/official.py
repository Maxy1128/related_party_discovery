"""Official HTML/PDF download, normalization, hashing, and persistence."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from PyPDF2 import PdfReader

from rpd.config import Settings
from rpd.http import Download, PublicHttpClient
from rpd.models import IngestedDocument, OfficialSource
from rpd.normalize import html_to_text, normalize_url


RIO_TINTO_OFFICIAL_SOURCES = (
    OfficialSource(
        key="annual_report_2024",
        title="Rio Tinto Annual Report 2024",
        url=(
            "https://www.riotinto.com/-/media/content/documents/invest/reports/"
            "annual-reports/2024-annual-report.pdf?rev=af45e9c438764f07ab7944c263ca3615"
        ),
        publisher="Rio Tinto",
        published_at="2025-02-19",
    ),
    OfficialSource(
        key="companies",
        title="Rio Tinto companies and affiliates",
        url="https://www.riotinto.com/utility/companies",
        publisher="Rio Tinto",
    ),
    OfficialSource(
        key="transparency",
        title="Rio Tinto transparency disclosures",
        url="https://www.riotinto.com/sustainability/ethics-compliance/transparency",
        publisher="Rio Tinto",
    ),
)


class OfficialDocumentIngestor:
    def __init__(
        self,
        settings: Settings,
        connection: sqlite3.Connection,
        http: PublicHttpClient | None = None,
    ):
        self.settings = settings
        self.connection = connection
        self.http = http or PublicHttpClient(settings)

    def ingest(self, source: OfficialSource) -> IngestedDocument:
        download = self.http.download(source.url)
        raw_content_hash = hashlib.sha256(download.content).hexdigest()
        media_type = self._detect_media_type(download, source.url)
        extension = ".pdf" if media_type == "application/pdf" else ".html"
        raw_absolute = self._content_path(
            "raw", source.source_type, raw_content_hash, extension
        )
        self._write_once(raw_absolute, download.content)

        normalized_text = self._normalize(download.content, media_type, raw_absolute)
        normalized_absolute: Path | None = None
        retrieval_status = "METADATA_ONLY"
        if normalized_text.strip():
            content_hash = hashlib.sha256(
                normalized_text.encode("utf-8")
            ).hexdigest()
            normalized_absolute = self._content_path(
                "normalized", source.source_type, content_hash, ".txt"
            )
            self._write_derived(
                normalized_absolute, normalized_text.encode("utf-8")
            )
            retrieval_status = "FULL_TEXT"
        else:
            content_hash = raw_content_hash

        normalized_source_url = normalize_url(download.final_url or source.url)
        row = self.connection.execute(
            "SELECT id FROM documents WHERE source_type = ? AND normalized_url = ?",
            (source.source_type, normalized_source_url),
        ).fetchone()
        if row:
            document_id = int(row["id"])
        else:
            document_id = int(
                self.connection.execute(
                    """
                    INSERT INTO documents(
                        source_type, title, publisher, original_url, normalized_url,
                        published_at, first_retrieved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source.source_type,
                        source.title,
                        source.publisher,
                        source.url,
                        normalized_source_url,
                        source.published_at,
                        download.retrieved_at,
                    ),
                ).lastrowid
            )
        existing = self.connection.execute(
            """
            SELECT id, raw_path, normalized_path, media_type, retrieval_status
            FROM document_versions
            WHERE document_id = ? AND content_hash = ?
            """,
            (document_id, content_hash),
        ).fetchone()
        if existing:
            return IngestedDocument(
                document_id=document_id,
                document_version_id=int(existing["id"]),
                content_hash=content_hash,
                raw_content_hash=raw_content_hash,
                raw_path=existing["raw_path"],
                normalized_path=existing["normalized_path"],
                media_type=existing["media_type"],
                retrieval_status=existing["retrieval_status"],
                retrieved_at=download.retrieved_at,
                reused=True,
            )
        self.connection.execute(
            "UPDATE document_versions SET is_current = 0 WHERE document_id = ?",
            (document_id,),
        )
        version_id = int(
            self.connection.execute(
                """
                INSERT INTO document_versions(
                    document_id, content_hash, raw_content_hash, media_type, byte_size, raw_path,
                    normalized_path, retrieval_status, retrieved_at, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    document_id,
                    content_hash,
                    raw_content_hash,
                    media_type,
                    len(download.content),
                    self._relative(raw_absolute),
                    self._relative(normalized_absolute) if normalized_absolute else None,
                    retrieval_status,
                    download.retrieved_at,
                ),
            ).lastrowid
        )
        return IngestedDocument(
            document_id=document_id,
            document_version_id=version_id,
            content_hash=content_hash,
            raw_content_hash=raw_content_hash,
            raw_path=self._relative(raw_absolute),
            normalized_path=self._relative(normalized_absolute) if normalized_absolute else None,
            media_type=media_type,
            retrieval_status=retrieval_status,
            retrieved_at=download.retrieved_at,
            reused=False,
        )

    def _content_path(
        self, area: str, source_type: str, content_hash: str, extension: str
    ) -> Path:
        base = getattr(self.settings.paths, area)
        return base / source_type.casefold() / content_hash[:2] / f"{content_hash}{extension}"

    @staticmethod
    def _write_once(path: Path, content: bytes) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    @staticmethod
    def _write_derived(path: Path, content: bytes) -> None:
        """Refresh reproducible derived text while preserving immutable raw files."""

        if path.exists() and path.read_bytes() == content:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.settings.paths.root).as_posix()

    @staticmethod
    def _detect_media_type(download: Download, url: str) -> str:
        if download.content.startswith(b"%PDF-"):
            return "application/pdf"
        if "pdf" in download.content_type or url.lower().split("?", 1)[0].endswith(".pdf"):
            return "application/pdf"
        return "text/html"

    def _normalize(self, content: bytes, media_type: str, raw_path: Path) -> str:
        if media_type == "application/pdf":
            return self._pdf_text(raw_path)
        return html_to_text(content)

    @staticmethod
    def _pdf_text(path: Path) -> str:
        pdftotext = shutil.which("pdftotext")
        if pdftotext:
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    output = Path(temp_dir) / "document.txt"
                    subprocess.run(
                        [pdftotext, "-layout", str(path), str(output)],
                        check=True,
                        capture_output=True,
                        timeout=240,
                    )
                    text = output.read_text(encoding="utf-8", errors="replace")
                    return OfficialDocumentIngestor._mark_pages(text.split("\f"))
            except (OSError, subprocess.SubprocessError):
                # Some Windows distributions bundle a pdftotext binary that can
                # crash on otherwise valid PDFs. The pure-Python fallback keeps
                # one parser failure from aborting the ingestion job.
                pass
        reader = PdfReader(str(path))
        return OfficialDocumentIngestor._mark_pages(
            [page.extract_text() or "" for page in reader.pages]
        )

    @staticmethod
    def _mark_pages(pages: list[str]) -> str:
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return "\n\n".join(
            f"--- PAGE {number} ---\n{text.strip()}"
            for number, text in enumerate(pages, start=1)
        )
