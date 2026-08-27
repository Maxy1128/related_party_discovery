"""Ingest and validate the reusable eight-document Rio Tinto smoke set."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import connect, initialize  # noqa: E402
from rpd.smoke import ingest_missing_smoke_documents, validate_smoke_documents  # noqa: E402


def main() -> None:
    settings = Settings.from_env()
    settings.paths.create()
    initialize(settings.paths.sqlite_path)
    with connect(settings.paths.sqlite_path) as connection:
        ingestion = ingest_missing_smoke_documents(settings, connection)
        validation = validate_smoke_documents(settings, connection)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_count": len(validation),
        "passed": sum(item["passed"] for item in validation),
        "failed": sum(not item["passed"] for item in validation),
        "ingestion": ingestion,
        "validation": validation,
        "live_llm_extraction": False,
        "api_keys_present": {
            "llm": bool(settings.llm_api_key), "tavily": bool(settings.tavily_api_key),
        },
        "note": "This document smoke command never writes candidate facts; live APIs are reserved for the end-to-end run.",
    }
    output = settings.paths.reports / "rio_tinto_smoke_test.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
