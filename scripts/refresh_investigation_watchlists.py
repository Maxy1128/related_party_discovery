"""Refresh all risk-list matches for entities connected to an investigation."""

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
    parser.add_argument("--investigation-id", required=True, type=int)
    args = parser.parse_args()
    settings = Settings.from_env()
    initialize(settings.paths.sqlite_path)
    with connect(settings.paths.sqlite_path) as connection:
        investigation = connection.execute(
            "SELECT target_entity_id FROM investigations WHERE id=?", (args.investigation_id,)
        ).fetchone()
        if not investigation:
            raise LookupError(f"Investigation not found: {args.investigation_id}")
        entity_ids = {investigation["target_entity_id"]}
        for row in connection.execute(
            """SELECT a.subject_entity_id,a.object_entity_id
               FROM investigation_assertions ia JOIN assertions a ON a.id=ia.assertion_id
               WHERE ia.investigation_id=?""",
            (args.investigation_id,),
        ):
            entity_ids.add(row["subject_entity_id"])
            if row["object_entity_id"] is not None:
                entity_ids.add(row["object_entity_id"])

        client = WatchlistClient(settings)
        matcher = WatchlistMatcher(connection, settings.watchlist_fuzzy_threshold)
        matches: set[int] = set()
        for list_name in WatchlistClient.URLS:
            print(f"Fetching {list_name}...", flush=True)
            snapshot = client.fetch(list_name)
            for entity_id in sorted(entity_ids):
                matches.update(matcher.match(entity_id, snapshot))
        connection.execute(
            """UPDATE investigation_steps SET status='COMPLETED',item_count=?,message=?,
               completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE investigation_id=? AND step_name='Risk lists'""",
            (len(matches), f"Checked {len(entity_ids)} connected entities against three public lists.",
             args.investigation_id),
        )
        connection.commit()
    print(f"Watchlist refresh complete: entities={len(entity_ids)} matches={len(matches)}", flush=True)


if __name__ == "__main__":
    main()
