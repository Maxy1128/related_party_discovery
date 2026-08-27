"""Deterministic names and URLs used for matching and deduplication."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        hostname = f"{hostname}:{port}"
    path = parts.path or "/"
    tracking_names = {"fbclid", "gclid", "mc_cid", "mc_eid"}
    query_items = [
        (name, item_value)
        for name, item_value in parse_qsl(parts.query, keep_blank_values=True)
        if not name.casefold().startswith("utm_") and name.casefold() not in tracking_names
    ]
    query = urlencode(sorted(query_items))
    return urlunsplit((scheme, hostname, path, query, ""))


def html_to_text(content: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
