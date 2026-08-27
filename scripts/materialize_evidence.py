"""Apply deterministic relation, evidence, confidence, and risk rules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.validation import EvidenceMaterializer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extraction-run-id", required=True, type=int)
    args = parser.parse_args()
    settings = Settings.from_env()
    initialize(settings.paths.sqlite_path)
    try:
        with connect(settings.paths.sqlite_path) as connection:
            result = EvidenceMaterializer(connection).materialize(args.extraction_run_id)
    except (KeyError, LookupError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"Evidence materialization complete: assertions={result['assertions']} "
        f"risk_events={result['risk_events']}"
    )


if __name__ == "__main__":
    main()
