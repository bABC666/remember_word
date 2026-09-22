"""Attribute a ``verify_backup`` failure to additive columns or to real changes.

``verify_backup.py`` reports a table as ``rows_changed`` when the row identities it
recomputes no longer match the baseline. Two very different situations produce
that message:

* a migration **added a column** to the table, so the identity is now computed
  over more columns than the baseline hashed (the pre-V1.2 baseline is a
  ``baseline_version 1`` snapshot that does not record its column list); or
* a column that both sides hash actually **changed value**, which is data loss.

Only the second is a problem, and the two are indistinguishable in the report.
This tool separates them. For every table it recomputes the live rows' hashes
using the columns that existed when the baseline was captured, so a table is
either proven intact over its recorded columns or proven changed, with the
differing rowids listed.

Read-only: it opens the live database with SQLite's ``mode=ro`` and writes
nothing. Exit code 1 means at least one table's recorded content really differs.

Usage::

    python tools/diagnose_backup_rows.py
    python tools/diagnose_backup_rows.py --baseline data/recovery/baseline.json
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "verified_db.py"
_spec = importlib.util.spec_from_file_location("verified_db", MODULE_PATH)
assert _spec and _spec.loader
verified_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verified_db)

DEFAULT_BASELINE = Path("data/recovery/baseline.json")
DEFAULT_DATABASE = Path("data/vocab.db")

#: Columns each table gained after the baseline was captured, by migration.
#: These are the bridge columns of 0005 and 0006; nothing else was added.
ADDED_AFTER_BASELINE: dict[str, frozenset[str]] = {
    "word": frozenset({"user_id", "lexicon_entry_id"}),
    "review_event": frozenset({"user_id", "lexicon_entry_id"}),
    "article": frozenset({"user_id"}),
    "article_word_exposure": frozenset({"lexicon_entry_id"}),
    "import_batch": frozenset({"user_id"}),
    "import_candidate": frozenset({"lexicon_entry_id"}),
    "history_event": frozenset({"user_id"}),
}


def columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'pragma table_info("{table}")')]


def row_hashes(connection: sqlite3.Connection, table: str, selected: list[str]) -> dict[str, str]:
    statement = f'select rowid, {", ".join(selected)} from "{table}"'
    return {
        str(row[0]): verified_db.row_hash(tuple(row[1:])) for row in connection.execute(statement)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args(argv)

    if not args.baseline.exists():
        print("baseline not found:", args.baseline)
        return 1
    if not args.database.exists():
        print("database not found:", args.database)
        return 1

    baseline = verified_db.load_baseline(args.baseline)
    connection = verified_db.connect_readonly(args.database)
    problems: list[str] = []

    print(f"database : {args.database}")
    print(f"baseline : {args.baseline} ({baseline.get('label')})")
    print(f"revision : baseline={baseline.get('alembic_revision')} "
          f"current={verified_db.revision(connection)}")
    print()
    print(f"{'table':26} {'class':12} {'rows':>5} {'cols':>5}  verdict")
    try:
        for table, info in baseline["tables"].items():
            recorded = info["row_hashes"]
            classification = info.get("classification", "append_only")
            if not recorded:
                print(f"{table:26} {classification:12} {0:>5} {'-':>5}  empty at capture")
                continue

            present = columns(connection, table)
            ignored = set(verified_db.IGNORED_COLUMNS.get(table, frozenset()))
            added = ADDED_AFTER_BASELINE.get(table, frozenset())
            as_captured = [name for name in present if name not in ignored and name not in added]
            as_now = [name for name in present if name not in ignored]

            hashes = row_hashes(connection, table, as_captured)
            changed = sorted(
                key for key in recorded if key in hashes and recorded[key] != hashes[key]
            )
            missing = sorted(key for key in recorded if key not in hashes)
            # Append-only tables legitimately gain rows while the app runs;
            # immutable tables must come back with exactly the same row set.
            unexpected = (
                sorted(key for key in hashes if key not in recorded)
                if classification == "immutable"
                else []
            )

            if changed or missing or unexpected:
                verdict = "CONTENT DIFFERS"
                problems.append(
                    f"{table}: changed={changed} missing={missing} unexpected={unexpected}"
                )
            elif len(hashes) > len(recorded):
                extra = sorted(set(as_now) - set(as_captured))
                suffix = f"; added columns: {extra}" if extra else ""
                verdict = (
                    f"intact, +{len(hashes) - len(recorded)} appended rows{suffix}"
                )
            else:
                extra = sorted(set(as_now) - set(as_captured))
                verdict = "identical" + (f" (added columns: {extra})" if extra else "")

            print(
                f"{table:26} {classification:12} {len(recorded):>5} {len(as_now):>5}  {verdict}"
            )
    finally:
        connection.close()

    print()
    if problems:
        print("CONTENT CHANGED SINCE THE BASELINE:")
        for problem in problems:
            print("  -", problem)
        return 1
    print("No recorded content changed on any table: every difference is an added column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
