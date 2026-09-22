"""Verify a SQLite database against a recorded baseline snapshot.

A copy is only called a **VERIFIED BACKUP** when it passes every check against a
baseline captured from a database that was itself verified. Hard-coded row
counts from a single moment in time are deliberately gone: they made a healthy
running database look broken, and worse, they would have looked "fine" for a
database that had lost its history and been refilled.

Usage::

    # record a baseline from a database that is currently trusted
    python tools/verify_backup.py data/vocab.db --write-baseline data/recovery/baseline.json

    # verify a database or backup against that baseline
    python tools/verify_backup.py data/backups/x.db --baseline data/recovery/baseline.json

See ``tools/verified_db.py`` for the immutable / append-only classification.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "verified_db.py"
_spec = importlib.util.spec_from_file_location("verified_db", MODULE_PATH)
assert _spec and _spec.loader
verified_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verified_db)

DEFAULT_BASELINE = Path("data/recovery/baseline.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a database or backup against a baseline snapshot"
    )
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=f"baseline snapshot to compare against (default: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="record a NEW baseline from this database instead of verifying it",
    )
    parser.add_argument("--label", default="", help="label stored in a new baseline")
    parser.add_argument("--notes", default="", help="free-form note stored in a new baseline")
    parser.add_argument(
        "--no-row-hashes",
        action="store_true",
        help="record counts only (weaker: cannot detect modified rows)",
    )
    parser.add_argument("--json", type=Path, default=None, help="write the report here")
    parser.add_argument(
        "--allow-revision-change",
        action="store_true",
        help="do not require the baseline alembic revision",
    )
    args = parser.parse_args(argv)

    if args.write_baseline is not None:
        snapshot = verified_db.capture_baseline(
            args.database,
            label=args.label or str(args.database),
            notes=args.notes,
            hash_rows=not args.no_row_hashes,
        )
        # Refuse to record a baseline from a database that is already unhealthy.
        if snapshot["integrity_check"] != "ok":
            print("REFUSING to record a baseline: integrity_check =", snapshot["integrity_check"])
            return 1
        if snapshot["foreign_key_check_violations"]:
            print(
                "REFUSING to record a baseline:",
                snapshot["foreign_key_check_violations"],
                "foreign key violations",
            )
            return 1
        verified_db.save_baseline(snapshot, args.write_baseline)
        print(f"baseline recorded : {args.write_baseline}")
        print(f"database          : {snapshot['database']}")
        print(f"bytes             : {snapshot['bytes']}")
        print(f"sha256            : {snapshot['sha256']}")
        print(f"alembic revision  : {snapshot['alembic_revision']}")
        print(f"integrity_check   : {snapshot['integrity_check']}")
        print("tables:")
        for name, info in snapshot["tables"].items():  # type: ignore[union-attr]
            print(
                "  %-24s %-12s rows=%-5s"
                % (name, info["classification"], info["rows"])
            )
        return 0

    if not args.baseline.exists():
        print("baseline not found:", args.baseline)
        print("record one first with --write-baseline")
        return 1

    baseline = verified_db.load_baseline(args.baseline)
    verified, report = verified_db.compare_against_baseline(
        args.database,
        baseline,
        require_revision=not args.allow_revision_change,
    )
    report["verdict"] = "VERIFIED BACKUP" if verified else "NOT A VERIFIED BACKUP"
    report["baseline_path"] = str(args.baseline)

    print(f"database : {report['database']}")
    print(f"bytes    : {report.get('bytes')}")
    print(f"sha256   : {report.get('sha256')}")
    print(f"baseline : {args.baseline} ({baseline.get('label')})")
    print(f"revision : {report.get('alembic_revision')}")
    print(f"integrity: {report.get('integrity_check')}")
    print(f"fk_check : {report.get('foreign_key_check_violations')} violations")
    print()
    print("per-table status:")
    for name, info in report.get("tables", {}).items():  # type: ignore[union-attr]
        print(
            "  %-24s %-12s baseline=%-5s current=%-5s %s"
            % (
                name,
                info.get("classification"),
                info.get("rows_baseline"),
                info.get("rows_current"),
                info.get("status"),
            )
        )
    if report["growth"]:  # type: ignore[union-attr]
        print()
        print("legitimate growth:")
        for item in report["growth"]:  # type: ignore[union-attr]
            print("  +", item)
    print()
    if verified:
        print("VERDICT  : VERIFIED BACKUP")
    else:
        print("VERDICT  : NOT A VERIFIED BACKUP")
        for failure in report["failures"]:  # type: ignore[union-attr]
            print("   FAIL:", failure)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("report   :", args.json)
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
