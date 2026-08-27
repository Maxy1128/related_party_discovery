"""Environment-only configuration and local path management."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> dict[str, str]:
    """Read a small local .env file without logging or third-party packages."""

    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {line_number}.")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid .env variable name on line {line_number}.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[name] = value
    return values


@dataclass(frozen=True)
class RuntimePaths:
    """All writable runtime locations used by the application."""

    root: Path
    raw: Path
    normalized: Path
    cache: Path
    database: Path
    reports: Path

    @classmethod
    def under(cls, root: Path) -> "RuntimePaths":
        resolved = root.expanduser().resolve()
        return cls(
            root=resolved,
            raw=resolved / "raw",
            normalized=resolved / "normalized",
            cache=resolved / "cache",
            database=resolved / "database",
            reports=resolved / "reports",
        )

    @property
    def sqlite_path(self) -> Path:
        return self.database / "evidence.db"

    def create(self) -> None:
        for directory in (
            self.root,
            self.raw,
            self.normalized,
            self.cache,
            self.database,
            self.reports,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    """Application settings loaded without reading a dotenv file."""

    paths: RuntimePaths
    llm_api_base: str = "https://n.tokeness.io/v1"
    llm_model: str = "gpt-5.4"
    embedding_model: str = "text-embedding-3-small"
    llm_api_key: str | None = field(default=None, repr=False)
    tavily_api_key: str | None = field(default=None, repr=False)
    gleif_api_base: str = "https://api.gleif.org/api/v1"
    wikidata_api_url: str = "https://www.wikidata.org/w/api.php"
    tavily_search_url: str = "https://api.tavily.com/search"
    http_connect_timeout_seconds: int = 10
    http_read_timeout_seconds: int = 60
    llm_read_timeout_seconds: int = 180
    http_user_agent: str = (
        "RelationshipDiscoveryMVP/0.1 (public-source relationship research)"
    )
    news_days: int = 90
    news_max_articles: int = 100
    request_concurrency: int = 2
    request_max_retries: int = 2
    extraction_chunk_chars: int = 20_000
    extraction_chunk_overlap_chars: int = 500
    entity_fuzzy_threshold: float = 0.96
    entity_fuzzy_margin: float = 0.08
    watchlist_fuzzy_threshold: float = 0.88
    ofac_sdn_xml_url: str = "https://www.treasury.gov/ofac/downloads/sdn.xml"
    uk_sanctions_csv_url: str = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"
    world_bank_debarred_url: str = "https://www.worldbank.org/en/projects-operations/procurement/debarred-firms"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        if environ is None:
            # Local secrets are optional. The process environment deliberately
            # wins so deployments can override workstation-only values.
            env = {
                **load_env_file(PROJECT_ROOT / ".env"),
                **load_env_file(PROJECT_ROOT / "local.env"),
                **os.environ,
            }
        else:
            env = environ
        data_root_value = env.get("RPD_DATA_DIR", "").strip()
        data_root = Path(data_root_value) if data_root_value else PROJECT_ROOT / "runtime"
        return cls(
            paths=RuntimePaths.under(data_root),
            llm_api_base=env.get(
                "GRAPHRAG_API_BASE", "https://n.tokeness.io/v1"
            ).rstrip("/"),
            llm_model=env.get("GRAPHRAG_LLM_MODEL", "gpt-5.4"),
            embedding_model=env.get(
                "GRAPHRAG_EMBEDDING_MODEL", "text-embedding-3-small"
            ),
            llm_api_key=env.get("GRAPHRAG_API_KEY") or env.get("OPENAI_API_KEY"),
            tavily_api_key=env.get("TAVILY_API_KEY"),
            gleif_api_base=env.get(
                "GLEIF_API_BASE", "https://api.gleif.org/api/v1"
            ).rstrip("/"),
            wikidata_api_url=env.get(
                "WIKIDATA_API_URL", "https://www.wikidata.org/w/api.php"
            ),
            tavily_search_url=env.get(
                "TAVILY_SEARCH_URL", "https://api.tavily.com/search"
            ),
            llm_read_timeout_seconds=int(
                env.get("GRAPHRAG_READ_TIMEOUT_SECONDS", "180")
            ),
        )

    def require_llm_key(self) -> str:
        if not self.llm_api_key:
            raise RuntimeError(
                "Set GRAPHRAG_API_KEY or OPENAI_API_KEY before running LLM extraction."
            )
        return self.llm_api_key

    def require_tavily_key(self) -> str:
        if not self.tavily_api_key:
            raise RuntimeError(
                "Set TAVILY_API_KEY before running a Tavily news search."
            )
        return self.tavily_api_key
