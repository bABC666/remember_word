"""Prove that running the test suite does not touch the real data directory.

Records a fingerprint of every file under ``data/`` before and after invoking
pytest, and reports any difference. This is the evidence that the isolation
guards work in practice, not just in unit tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path("data")
PYTHON = Path("backend/.venv/Scripts/python.exe")
BACKEND = Path("backend")


def fingerprint(root: Path) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        stat = path.stat()
        snapshot[str(path)] = {
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest.hexdigest(),
        }
    return snapshot


def main() -> int:
    print("fingerprinting", DATA_DIR, "...")
    before = fingerprint(DATA_DIR)
    print(f"  files: {len(before)}")

    print("running pytest ...")
    result = subprocess.run(
        [str(PYTHON), "-m", "pytest", "tests", "-q"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        check=False,
    )
    tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    print(f"  exit code: {result.returncode}")
    print(f"  summary  : {tail}")
    if result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:])

    print("fingerprinting again ...")
    after = fingerprint(DATA_DIR)

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(
        path for path in set(before) & set(after) if before[path] != after[path]
    )

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "pytest_exit_code": result.returncode,
        "pytest_summary": tail,
        "files_before": len(before),
        "files_after": len(after),
        "added": added,
        "removed": removed,
        "changed": changed,
        "untouched": not (added or removed or changed),
    }
    # The evidence must live OUTSIDE the tree it claims to protect. Writing it
    # into data/recovery/ made the proof change the very directory it reported on,
    # so its own "no changes" claim could never have included itself.
    artifacts = Path(
        os.environ.get("VOCAB_TEST_ARTIFACTS_DIR", "test-artifacts")
    ).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    evidence_path = artifacts / "pytest-isolation-evidence.json"
    evidence_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print("added  :", added or "none")
    print("removed:", removed or "none")
    print("changed:", changed or "none")
    print()
    if report["untouched"]:
        print("EVIDENCE: pytest did NOT touch the real data directory")
    else:
        print("EVIDENCE: pytest MODIFIED the real data directory -- investigate")
    print("report  :", evidence_path)
    return 0 if report["untouched"] and result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
