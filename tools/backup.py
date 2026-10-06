"""Standalone backup runner (Python 3.11+); shares the app.cli backup command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.backup import command_backup, configure_parser


def main() -> int:
    parser = argparse.ArgumentParser(description="Shici validated SQLite backup")
    configure_parser(parser)
    return command_backup(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
