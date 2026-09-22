"""Seal the restored database: make it self-contained and re-verify."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "verify_backup.py"
_spec = importlib.util.spec_from_file_location("verify_backup", MODULE_PATH)
assert _spec and _spec.loader
verify_backup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_backup)
verify = verify_backup.verify

LIVE = Path("data/vocab.db")


def main() -> int:
    for sidecar in (LIVE.with_name(LIVE.name + "-wal"), LIVE.with_name(LIVE.name + "-shm")):
        if sidecar.exists():
            sidecar.unlink()
            print("removed sidecar:", sidecar)

    connection = sqlite3.connect(str(LIVE))
    print("journal_mode :", connection.execute("pragma journal_mode").fetchone()[0])
    print("checkpoint   :", connection.execute("pragma wal_checkpoint(TRUNCATE)").fetchall())
    connection.close()

    verified, report = verify(LIVE, expected_revision="0003_article_reading_tools")
    Path("data/recovery/vocab-live-sealed.verify.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print("bytes  :", report["bytes"])
    print("sha256 :", report["sha256"])
    for name, value in report["checks"].items():
        print("  %-28s %s" % (name, value))
    print()
    print("VERDICT:", "VERIFIED" if verified else "NOT VERIFIED")
    for failure in report["failures"]:
        print("  FAIL:", failure)
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
