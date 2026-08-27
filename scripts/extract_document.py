"""Run generic structured extraction for one stored full-text document version."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.extraction import ExtractionService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-version-id", required=True, type=int)
    args = parser.parse_args()
    settings = Settings.from_env()
    try:
        settings.require_llm_key()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    initialize(settings.paths.sqlite_path)
    with connect(settings.paths.sqlite_path) as connection:
        result = ExtractionService(settings, connection).extract_document(
            args.document_version_id
        )
    print(f"Structured extraction complete: chunks={len(result['chunks'])}")


if __name__ == "__main__":
    main()
