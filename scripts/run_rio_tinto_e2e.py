"""Run a bounded, real Rio Tinto investigation through the generic pipeline."""

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
from rpd.orchestrator import InvestigationOrchestrator, InvestigationRequest  # noqa: E402
from rpd.reporting import ReportBuilder  # noqa: E402
from rpd.smoke import RIO_TINTO_SMOKE_DOCUMENTS  # noqa: E402


RIO_TINTO_PLC_LEI = "213800YOEO5OQ72G2R82"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-documents", type=int, default=6)
    parser.add_argument("--max-news", type=int, default=2)
    parser.add_argument("--skip-watchlists", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.require_llm_key()
    settings.require_tavily_key()
    settings.paths.create()
    initialize(settings.paths.sqlite_path)

    def progress(step: str, message: str) -> None:
        print(f"[{step}] {message}", flush=True)

    request = InvestigationRequest(
        company_query="Rio Tinto",
        selected_lei=RIO_TINTO_PLC_LEI,
        official_urls=tuple(item.source.url for item in RIO_TINTO_SMOKE_DOCUMENTS),
        include_news=True,
        include_watchlists=not args.skip_watchlists,
        max_extraction_documents=args.max_documents,
        max_news_extraction_documents=args.max_news,
    )
    with connect(settings.paths.sqlite_path) as connection:
        investigation_id = InvestigationOrchestrator(settings, connection).run(request, progress)
        builder = ReportBuilder(connection)
        view = builder.load(investigation_id)
        markdown_path = settings.paths.reports / f"rio_tinto_investigation_{investigation_id}.md"
        html_path = settings.paths.reports / f"rio_tinto_investigation_{investigation_id}.html"
        markdown_path.write_text(builder.markdown(view), encoding="utf-8")
        html_path.write_text(builder.html(view), encoding="utf-8")
        summary = {
            "investigation_id": investigation_id,
            "status": view.investigation["status"],
            "model": settings.llm_model,
            "documents": len(view.documents),
            "relationships": len(view.relationships),
            "risk_events": len(view.risk_events),
            "watchlist_matches": len(view.watchlist_matches),
            "markdown_report": str(markdown_path),
            "html_report": str(html_path),
        }
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
