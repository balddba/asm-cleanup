#!/usr/bin/env python3
"""Rebuild the committed documentation demo SQLite database.

Writes fictional baldba.com screenshot data to docs/demo/asm_cleanup_demo.db
(or --output). Refuses production database filenames.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and rebuild the demo database file.

    Args:
        argv (list[str] | None): Optional argument overrides.

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Rebuild docs/demo/asm_cleanup_demo.db for documentation screenshots."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Destination SQLite path (default: docs/demo/asm_cleanup_demo.db)",
    )
    args = parser.parse_args(argv)

    try:
        from asm_cleanup.db import ProductionDatabaseError, build_demo_database
    except ImportError:
        sys.stderr.write(
            "Error: web optional dependencies are required.\n"
            "Install with: uv sync --extra web\n"
        )
        return 1

    try:
        dest = build_demo_database(args.output)
    except ProductionDatabaseError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    print(f"Wrote demo database: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
