"""Backup and restore verification tooling for 拾词.

A copy is only called a *verified backup* when :func:`verify` passes every
check. A file that merely exists, or whose copy "succeeded", proves nothing:
the ``pre-p1.2-*`` copy taken before the P1.2 migration was itself already
damaged, and it was almost trusted as a safety net.

Usage::

    python tools/verify_backup.py <database> [--expect-revision 0003_...]
                                                [--no-count-check] [--json out.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

#: Row counts of the V1.1 production database after the 2026-09-22 restore.
V1_1_EXPECTED_COUNTS = {
    "word": 19,
    "review_event": 10,
    "article": 1,
    "article_word_exposure": 16,
    "article_word_lookup": 0,
    "import_batch": 1,
    "import_image": 2,
    "import_candidate": 19,
    "history_event": 2,
    "app_setting": 4,
}

V1_1_REQUIRED_TABLES = set(V1_1_EXPECTED_COUNTS) | {"alembic_version"}

#: Fields whose values must be non-degenerate for the data to be usable.
FINGERPRINT_QUERIES = {
    "word_nonempty_anchor": "select count(*) from word where anchor <> ''",
    "word_nonempty_source_meanings": (
        "select count(*) from word where source_meanings not in ('', '[]')"
    ),
    "word_nonempty_source_raw": "select count(*) from word where source_raw <> ''",
    "word_status_distribution": "select group_concat(status || ':' || n, ',') from ("
    "select status, count(*) as n from word group by status order by status)",
    "review_event_results": "select group_concat(result || ':' || n, ',') from ("
    "select result, count(*) as n from review_event group by result order by result)",
    "article_completed": "select count(*) from article where completed = 1",
    "article_content_length": "select coalesce(max(length(content)), 0) from article",
    "confirmed_candidates": "select count(*) from import_candidate where confirmed = 1",
    "candidates_linked_to_words": (
        "select count(*) from import_candidate where word_id is not null"
    ),
    "exposure_distinct_words": "select count(distinct word_id) from article_word_exposure",
    "import_batch_ocr_length": "select coalesce(max(length(raw_ocr_text)), 0) from import_batch",
    "import_image_paths": (
        "select group_concat(file_path, '|') from import_image order by id"
    ),
    "app_setting_keys": (
        "select group_concat(key, ',') from (select key from app_setting order by key)"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(
    database: Path,
    *,
    expected_revision: str | None = "0003_article_reading_tools",
    expected_counts: dict[str, int] | None = None,
) -> tuple[bool, dict[str, object]]:
    counts = expected_counts if expected_counts is not None else V1_1_EXPECTED_COUNTS
    report: dict[str, object] = {"path": str(database), "checks": {}, "failures": []}
    checks: dict[str, object] = report["checks"]  # type: ignore[assignment]
    failures: list[str] = report["failures"]  # type: ignore[assignment]

    if not database.exists():
        failures.append("file does not exist")
        report["verified"] = False
        return False, report

    report["bytes"] = database.stat().st_size
    report["sha256"] = sha256(database)

    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        failures.append(f"cannot open: {error}")
        report["verified"] = False
        return False, report

    try:
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        checks["integrity_check"] = integrity
        if integrity != "ok":
            failures.append(f"integrity_check = {integrity}")

        foreign_keys = connection.execute("pragma foreign_key_check").fetchall()
        checks["foreign_key_check_violations"] = len(foreign_keys)
        if foreign_keys:
            failures.append(f"foreign_key_check found {len(foreign_keys)} violations")

        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
            )
        }
        checks["tables"] = sorted(tables)
        missing = sorted(V1_1_REQUIRED_TABLES - tables)
        if missing:
            failures.append(f"missing tables: {missing}")

        if expected_revision is not None:
            try:
                revision = connection.execute(
                    "select version_num from alembic_version"
                ).fetchone()
                checks["alembic_revision"] = revision[0] if revision else None
                if revision is None or revision[0] != expected_revision:
                    failures.append(
                        f"alembic revision {checks['alembic_revision']!r} != {expected_revision!r}"
                    )
            except sqlite3.Error as error:
                failures.append(f"alembic_version unreadable: {error}")

        actual_counts: dict[str, int] = {}
        for table, expected in counts.items():
            if table not in tables:
                actual_counts[table] = -1
                continue
            value = connection.execute(f'select count(*) from "{table}"').fetchone()[0]
            actual_counts[table] = value
            if value != expected:
                failures.append(f"{table}: {value} != expected {expected}")
        checks["row_counts"] = actual_counts

        fingerprint: dict[str, object] = {}
        for name, query in FINGERPRINT_QUERIES.items():
            try:
                fingerprint[name] = connection.execute(query).fetchone()[0]
            except sqlite3.Error as error:
                fingerprint[name] = f"error: {error}"
                failures.append(f"fingerprint {name} failed: {error}")
        checks["fingerprint"] = fingerprint
    finally:
        connection.close()

    report["verified"] = not failures
    return not failures, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a SQLite backup before trusting it")
    parser.add_argument("database", type=Path)
    parser.add_argument("--expect-revision", default="0003_article_reading_tools")
    parser.add_argument("--no-count-check", action="store_true")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    verified, report = verify(
        args.database,
        expected_revision=args.expect_revision,
        expected_counts=None if args.no_count_check else V1_1_EXPECTED_COUNTS,
    )
    report["label"] = args.label
    report["verified_at_utc"] = datetime.now(UTC).isoformat()
    report["verdict"] = "VERIFIED BACKUP" if verified else "NOT A VERIFIED BACKUP"

    print(f"database : {report['path']}")
    print(f"bytes    : {report.get('bytes')}")
    print(f"sha256   : {report.get('sha256')}")
    for name, value in report["checks"].items():  # type: ignore[union-attr]
        print(f"{name:26}: {value}")
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
