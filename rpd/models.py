"""Small source-neutral records shared by ingestion adapters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParentReference:
    relationship: str
    status: str
    parent_lei: str | None = None
    exception_reason: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class IdentityProfile:
    canonical_name: str
    source: str
    source_url: str
    legal_name: str | None = None
    lei: str | None = None
    registration_number: str | None = None
    registration_authority: str | None = None
    country_code: str | None = None
    registered_address: str | None = None
    website: str | None = None
    aliases: tuple[str, ...] = ()
    identifiers: tuple[tuple[str, str], ...] = ()
    parents: tuple[ParentReference, ...] = ()
    raw: dict = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class OfficialSource:
    key: str
    title: str
    url: str
    publisher: str
    source_type: str = "OFFICIAL_DOCUMENT"
    published_at: str | None = None


@dataclass(frozen=True)
class IngestedDocument:
    document_id: int
    document_version_id: int
    content_hash: str
    raw_content_hash: str
    raw_path: str
    normalized_path: str | None
    media_type: str
    retrieval_status: str
    retrieved_at: str
    reused: bool


@dataclass(frozen=True)
class NewsArticle:
    title: str
    url: str
    query: str
    score: float
    published_at: str | None = None
    summary: str = ""
    raw_content: str | None = None
    full_text_source: str = "METADATA_ONLY"
    search_days: int = 90
    source_bytes: bytes | None = field(default=None, repr=False, compare=False)
    source_media_type: str | None = None
