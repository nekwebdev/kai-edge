#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def prepare_import_path() -> None:
    script_path = Path(__file__).resolve()
    candidates = [
        script_path.parent.parent / "app",
        script_path.parent.parent,
    ]

    for candidate in candidates:
        package_path = candidate / "kai_edge"
        if package_path.is_dir():
            sys.path.insert(0, str(candidate))
            return


prepare_import_path()

from kai_edge.cli.trigger import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
