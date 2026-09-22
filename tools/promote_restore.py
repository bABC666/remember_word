"""Promote the verified V1.1 restore candidate to the live database.

Safety order:
1. re-verify the candidate immediately before promoting it;
2. move the damaged database *and its WAL/SHM* aside (a stale WAL would replay
   frames against the restored file);
3. copy the candidate into place;
4. re-verify the live file.
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

CANDIDATE = Path("data/recovery/vocab-restored-v1.1.db")
LIVE = Path("data/vocab.db")
ASIDE = Path("_INCIDENT-20260922/corrupted-live-db")


def main() -> int:
    print("step 1: re-verify the candidate")
    verified, report = verify(CANDIDATE, expected_revision="0003_article_reading_tools")
    if not verified:
        print("  FAILED:", report["failures"])
        return 1
    print("  ok, sha256:", report["sha256"])

    print("step 2: move the damaged database and its WAL/SHM aside")
    ASIDE.mkdir(parents=True, exist_ok=True)
    for name, suffix in (
        ("vocab.db", "db"),
        ("vocab.db-wal", "db-wal"),
        ("vocab.db-shm", "db-shm"),
    ):
        source = Path("data") / name
        if not source.exists():
            print(f"  {name}: absent")
            continue
        destination = ASIDE / f"vocab-CORRUPTED-20260922.{suffix}"
        if destination.exists():
            destination.unlink()
        shutil.move(str(source), str(destination))
        print(f"  {name} -> {destination} ({destination.stat().st_size} bytes)")

    print("step 3: promote the verified candidate")
    shutil.copy2(CANDIDATE, LIVE)
    print(f"  {LIVE} ({LIVE.stat().st_size} bytes)")

    print("step 4: verify the live database")
    verified, live_report = verify(LIVE, expected_revision="0003_article_reading_tools")
    Path("data/recovery/vocab-live-after-restore.verify.json").write_text(
        json.dumps(live_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name, value in live_report["checks"].items():
        print("  %-28s %s" % (name, value))
    print("  VERDICT:", "VERIFIED" if verified else "NOT VERIFIED")
    for sidecar in ("data/vocab.db-wal", "data/vocab.db-shm"):
        print(f"  {sidecar} present: {Path(sidecar).exists()}")
    for failure in live_report["failures"]:
        print("  FAIL:", failure)
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
