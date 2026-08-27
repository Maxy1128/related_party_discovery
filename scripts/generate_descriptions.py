"""Generate or backfill versioned descriptions for an existing investigation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.descriptions import DescriptionService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--investigation-id", required=True, type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--incremental", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.require_llm_key()
    initialize(settings.paths.sqlite_path)
    with connect(settings.paths.sqlite_path) as connection:
        result = DescriptionService(settings, connection, batch_size=args.batch_size).generate(
            args.investigation_id, force=args.force, incremental=args.incremental
        )
        total = result.entity_descriptions + result.relationship_descriptions + result.cached
        connection.execute(
            """INSERT INTO investigation_steps(investigation_id,step_name,status,item_count,
               message,completed_at) VALUES (?,'Descriptions','COMPLETED',?,?,
               strftime('%Y-%m-%dT%H:%M:%fZ','now'))
               ON CONFLICT(investigation_id,step_name) DO UPDATE SET
                 status='COMPLETED',item_count=excluded.item_count,message=excluded.message,
                 completed_at=excluded.completed_at,
                 updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (args.investigation_id, total,
             f"Prepared {total} entity and relationship description(s); {result.cached} reused from cache."),
        )
        connection.commit()
    print(json.dumps({
        "investigation_id": args.investigation_id,
        "model": settings.llm_model,
        "entity_descriptions_generated": result.entity_descriptions,
        "relationship_descriptions_generated": result.relationship_descriptions,
        "cached": result.cached,
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
