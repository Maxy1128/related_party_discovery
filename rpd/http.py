"""Conservative HTTP client for public data sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from rpd.config import Settings


@dataclass(frozen=True)
class Download:
    final_url: str
    content: bytes
    content_type: str
    retrieved_at: str
    headers: Mapping[str, str]


class PublicHttpClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        retry = Retry(
            total=settings.request_max_retries,
            connect=settings.request_max_retries,
            read=settings.request_max_retries,
            status=settings.request_max_retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            # LLM chat requests are stateless for this application and safe to
            # retry when a gateway fails before returning a usable response.
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(
            {"User-Agent": settings.http_user_agent, "Accept-Language": "en"}
        )

    @property
    def timeout(self) -> tuple[int, int]:
        return (
            self.settings.http_connect_timeout_seconds,
            self.settings.http_read_timeout_seconds,
        )

    def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> dict:
        response = self.session.get(url, params=params, timeout=self.timeout)
        if not response.ok:
            # Gateways often return the only actionable explanation in the
            # response body (for example, a rejected parameter or content
            # filter). Keep it bounded and never include request headers.
            detail = response.text[:500].replace("\n", " ").strip()
            raise requests.HTTPError(
                f"{response.status_code} {response.reason}: {detail}",
                response=response,
            )
        return response.json()

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
        timeout: tuple[int, int] | None = None,
    ) -> dict:
        response = self.session.post(
            url,
            json=dict(payload),
            headers=dict(headers or {}),
            timeout=timeout or self.timeout,
        )
        if not response.ok:
            detail = response.text[:500].replace("\n", " ").strip()
            raise requests.HTTPError(
                f"{response.status_code} {response.reason}: {detail}",
                response=response,
            )
        return response.json()

    def download(self, url: str, max_bytes: int = 50 * 1024 * 1024) -> Download:
        with self.session.get(url, timeout=self.timeout, stream=True) as response:
            response.raise_for_status()
            declared_size = response.headers.get("content-length")
            if declared_size and int(declared_size) > max_bytes:
                raise ValueError(
                    f"Document exceeds the {max_bytes}-byte download limit."
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=128 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(
                        f"Document exceeds the {max_bytes}-byte download limit."
                    )
                chunks.append(chunk)
            return Download(
                final_url=response.url,
                content=b"".join(chunks),
                content_type=response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower(),
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                headers=dict(response.headers),
            )
