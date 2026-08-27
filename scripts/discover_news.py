"""Run generic company news discovery and persist the resulting documents."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.sources.tavily import NewsDiscoveryService, NewsRepository  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, help="Company name to investigate")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    try:
        settings.require_tavily_key()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    settings.paths.create()
    initialize(settings.paths.sqlite_path)
    articles = NewsDiscoveryService(settings).discover(args.company)
    with connect(settings.paths.sqlite_path) as connection:
        repository = NewsRepository(settings, connection)
        version_ids = [repository.persist(article) for article in articles]
    full_text = sum(bool(article.raw_content) for article in articles)
    metadata_only = len(articles) - full_text
    print(
        f"News discovery complete: company={args.company!r} articles={len(articles)} "
        f"full_text={full_text} metadata_only={metadata_only} "
        f"document_versions={len(set(version_ids))}"
    )


if __name__ == "__main__":
    main()
