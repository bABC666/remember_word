"""Restore the 08:58 V1.1 backup into an independent file and verify it.

Nothing is written to data/vocab.db here: the restore target is a separate file
so it can be verified before it replaces the live database.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "verify_backup.py"
_spec = importlib.util.spec_from_file_location("verify_backup", MODULE_PATH)
assert _spec and _spec.loader
verify_backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_backup)
verify = verify_backup.verify

SOURCE = Path("data/backups/2026-09-22-vocab.db")
TARGET = Path("data/recovery/vocab-restored-v1.1.db")
REPORT = Path("data/recovery/vocab-restored-v1.1.verify.json")


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, TARGET)
    print(f"copied {SOURCE} -> {TARGET} ({TARGET.stat().st_size} bytes)\n")

    verified, report = verify(TARGET, expected_revision="0003_article_reading_tools")
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("database :", report["path"])
    print("bytes    :", report.get("bytes"))
    print("sha256   :", report.get("sha256"))
    print()
    for name, value in report["checks"].items():
        print("  %-28s %s" % (name, value))
    print()
    print("VERDICT  :", "VERIFIED BACKUP" if verified else "NOT A VERIFIED BACKUP")
    for failure in report["failures"]:
        print("  FAIL:", failure)
    print()
    print("report   :", REPORT)
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
