"""Create the local runtime directories and shared evidence database."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rpd.config import Settings  # noqa: E402
from rpd.db import initialize  # noqa: E402


def main() -> None:
    settings = Settings.from_env()
    settings.paths.create()
    initialize(settings.paths.sqlite_path)
    print(f"Initialized shared evidence database: {settings.paths.sqlite_path}")


if __name__ == "__main__":
    main()
