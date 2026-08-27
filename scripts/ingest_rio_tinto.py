"""Ingest Rio Tinto identity records and selected official sources."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.repository import IdentityRepository  # noqa: E402
from rpd.sources.gleif import GleifClient  # noqa: E402
from rpd.sources.official import (  # noqa: E402
    RIO_TINTO_OFFICIAL_SOURCES,
    OfficialDocumentIngestor,
)
from rpd.sources.wikidata import WikidataClient  # noqa: E402


RIO_TINTO_PLC_LEI = "213800YOEO5OQ72G2R82"
RIO_TINTO_PLC_WIKIDATA_ID = "Q10291918"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--official",
        action="append",
        choices=[source.key for source in RIO_TINTO_OFFICIAL_SOURCES],
        help="Official source key to ingest; repeat for multiple sources.",
    )
    parser.add_argument(
        "--all-official", action="store_true", help="Ingest every configured source."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.from_env()
    settings.paths.create()
    initialize(settings.paths.sqlite_path)
    with connect(settings.paths.sqlite_path) as connection:
        repository = IdentityRepository(connection)
        gleif_profile = GleifClient(settings).get(RIO_TINTO_PLC_LEI)
        entity_id = repository.upsert(gleif_profile)
        wikidata_profile = WikidataClient(settings).get(RIO_TINTO_PLC_WIKIDATA_ID)
        repository.upsert(wikidata_profile, entity_id=entity_id)
        print(
            f"Identity: entity_id={entity_id} legal_name={gleif_profile.legal_name} "
            f"lei={gleif_profile.lei}"
        )
        for parent in gleif_profile.parents:
            detail = parent.parent_lei or parent.exception_reason or "not reported"
            print(f"GLEIF {parent.relationship}: {parent.status} ({detail})")

        selected = RIO_TINTO_OFFICIAL_SOURCES if args.all_official else tuple(
            source for source in RIO_TINTO_OFFICIAL_SOURCES if source.key in (args.official or [])
        )
        ingestor = OfficialDocumentIngestor(settings, connection)
        for source in selected:
            result = ingestor.ingest(source)
            print(
                f"Official source: {source.key} version_id={result.document_version_id} "
                f"status={result.retrieval_status} reused={result.reused}"
            )


if __name__ == "__main__":
    main()
