"""Resolve candidates and mentions from one successful extraction run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.resolution import EntityResolver  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extraction-run-id", required=True, type=int)
    args = parser.parse_args()
    settings = Settings.from_env()
    initialize(settings.paths.sqlite_path)
    try:
        with connect(settings.paths.sqlite_path) as connection:
            result = EntityResolver(settings, connection).resolve_extraction_run(
                args.extraction_run_id
            )
    except (LookupError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"Entity resolution complete: candidates={result['candidates']} "
        f"mentions={result['mentions']}"
    )


if __name__ == "__main__":
    main()
