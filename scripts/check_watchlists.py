"""Check one resolved entity against selected public risk lists."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.watchlists import WatchlistClient, WatchlistMatcher  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entity-id", required=True, type=int)
    parser.add_argument(
        "--list", action="append", choices=("OFAC", "UK_SANCTIONS", "WORLD_BANK"),
        dest="lists", help="Repeat to select lists; defaults to all three.",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.paths.create()
    initialize(settings.paths.sqlite_path)
    client = WatchlistClient(settings)
    selected = args.lists or ["OFAC", "UK_SANCTIONS", "WORLD_BANK"]
    with connect(settings.paths.sqlite_path) as connection:
        matcher = WatchlistMatcher(connection, settings.watchlist_fuzzy_threshold)
        for list_name in selected:
            snapshot = client.fetch(list_name)
            match_ids = matcher.match(args.entity_id, snapshot)
            print(
                f"{list_name}: records={len(snapshot.records)} "
                f"leads={len(match_ids)} retrieved_at={snapshot.retrieved_at}"
            )


if __name__ == "__main__":
    main()
