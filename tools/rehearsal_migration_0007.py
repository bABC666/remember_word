"""Rehearse the 0006 -> 0007 migration on a disposable clone of production.

0007 rebuilds nine SQLite tables to attach the foreign keys the ORM declares, so it
cannot be rehearsed on the live database and cannot be tested by the ordinary test
suite either: those databases are either empty or built from the frozen V1.1 source,
and this migration has to cope with *production's* actual shape -- 0006, 18 tables,
the real rows, the real indexes.

What this does, and nothing else:

1. copies ``data/vocab.db`` to ``data/staging/migration-rehearsal-0006.db`` and
   proves the copy is byte-identical and logically identical;
2. runs ``alembic upgrade 0007_bridge_foreign_keys`` against **that file only**, via
   an absolute ``-x db_url``, from a subprocess whose data directory is the staging
   one;
3. compares revision, schema, row counts and the full content of every table before
   and after;
4. compares ``PRAGMA foreign_key_list`` against SQLAlchemy's metadata in both
   directions, including every delete rule.

The live database is opened read-only and never written. There is no downgrade path
here and no ``upgrade head``: the target revision is explicit, so Alembic validates
that the database really is at 0006 first.

Usage::

    python tools/rehearsal_migration_0007.py
    python tools/rehearsal_migration_0007.py --json data/recovery/rehearsal.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"
SOURCE = PROJECT_ROOT / "data" / "vocab.db"
STAGING_DIR = PROJECT_ROOT / "data" / "staging"
REHEARSAL = STAGING_DIR / "migration-rehearsal-0006.db"
FROM_REVISION = "0006_article_exposure_entry"
TO_REVISION = "0007_bridge_foreign_keys"

#: The nine bridge columns 0007 attaches, and the parent each must reference.
BRIDGES: tuple[tuple[str, str, str], ...] = (
    ("word", "user_id", "user"),
    ("word", "lexicon_entry_id", "lexicon_entry"),
    ("review_event", "user_id", "user"),
    ("review_event", "lexicon_entry_id", "lexicon_entry"),
    ("article", "user_id", "user"),
    ("article_word_exposure", "lexicon_entry_id", "lexicon_entry"),
    ("import_batch", "user_id", "user"),
    ("import_candidate", "lexicon_entry_id", "lexicon_entry"),
    ("history_event", "user_id", "user"),
)

#: Tables the task asks about by name (all of them are compared anyway).
SPOT_TABLES = (
    "word",
    "review_event",
    "article",
    "article_word_exposure",
    "article_word_lookup",
    "import_batch",
    "import_image",
    "import_candidate",
    "history_event",
    "lexicon",
    "lexicon_entry",
    "user_word_state",
)

failures: list[str] = []
report: dict[str, object] = {}


def say(message: str = "") -> None:
    print(message, flush=True)


def check(name: str, ok: bool, detail: object = "") -> bool:
    if not ok:
        failures.append(f"{name}: {detail}")
    marker = "PASS" if ok else "FAIL"
    suffix = "" if ok else f"  -> {detail}"
    say(f"  [{marker}] {name}{suffix}")
    return ok


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}{'?mode=ro' if read_only else ''}"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def tables(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type='table' order by name"
        )
    ]


def revision(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute("select version_num from alembic_version")]


def schema(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return sorted(
        (row[0], row[1], row[2] or "")
        for row in connection.execute("select type, name, sql from sqlite_master")
    )


def column_names(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'pragma table_info("{table}")')]


def row_fingerprint(connection: sqlite3.Connection, table: str) -> tuple[int, str]:
    """(row count, sha256 over every row's values in primary-key order)."""
    columns = column_names(connection, table)
    order = "key" if "key" in columns else ("id" if "id" in columns else "rowid")
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(
        f'select {", ".join(columns)} from "{table}" order by {order}'
    ):
        digest.update("\x1f".join("" if value is None else repr(value) for value in row).encode())
        digest.update(b"\x1e")
        count += 1
    return count, digest.hexdigest()


def physical_foreign_keys(connection: sqlite3.Connection, table: str) -> dict[str, tuple[str, str]]:
    return {
        row["from"]: (row["table"], row["on_delete"].upper())
        for row in connection.execute(f'pragma foreign_key_list("{table}")')
    }


def orm_foreign_keys() -> dict[str, dict[str, tuple[str, str]]]:
    sys.path.insert(0, str(BACKEND))
    import app.models  # noqa: F401  (populates Base.metadata)
    from app.db import Base

    declared: dict[str, dict[str, tuple[str, str]]] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            for foreign_key in column.foreign_keys:
                declared.setdefault(table.name, {})[column.name] = (
                    foreign_key.column.table.name,
                    (foreign_key.ondelete or "NO ACTION").upper(),
                )
    return declared


# --- phase 1: the clone ----------------------------------------------------


def phase1_clone() -> dict[str, object]:
    say("== Phase 1: create the rehearsal clone ==")
    source_stat = SOURCE.stat()
    source_sha = sha256_file(SOURCE)
    say(f"  source      : {SOURCE}")
    say(f"  bytes       : {source_stat.st_size}")
    say(f"  sha256      : {source_sha}")

    wal = Path(str(SOURCE) + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    say(f"  wal bytes   : {wal_bytes}")
    if wal_bytes:
        # A raw copy would then be missing committed pages, so refuse rather than
        # produce a clone that is quietly not production's state.
        raise SystemExit(
            "REFUSING: the write-ahead log is not empty, so a raw file copy would not "
            "be production's committed state. Take the copy with SQLite's backup API."
        )

    connection = connect(SOURCE, read_only=True)
    try:
        source_revision = revision(connection)
        source_integrity = connection.execute("pragma integrity_check").fetchone()[0]
        source_tables = tables(connection)
        before = {table: row_fingerprint(connection, table) for table in source_tables}
    finally:
        connection.close()

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        sidecar = Path(str(REHEARSAL) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy2(SOURCE, REHEARSAL)
    rehearsal_sha = sha256_file(REHEARSAL)

    say()
    say(f"  rehearsal   : {REHEARSAL}")
    say(f"  bytes       : {REHEARSAL.stat().st_size}")
    say(f"  sha256      : {rehearsal_sha}")
    check("rehearsal is byte-identical to production", rehearsal_sha == source_sha,
          f"{source_sha} vs {rehearsal_sha}")
    check("source revision is the expected 0006", source_revision == [FROM_REVISION], source_revision)
    check("source integrity_check is ok", source_integrity == "ok", source_integrity)
    return {
        "source_path": str(SOURCE),
        "source_sha256": source_sha,
        "source_bytes": source_stat.st_size,
        "rehearsal_path": str(REHEARSAL),
        "rehearsal_sha256": rehearsal_sha,
        "rehearsal_bytes": REHEARSAL.stat().st_size,
        "wal_bytes": wal_bytes,
        "before_revision": source_revision,
        "before_tables": source_tables,
        "before_fingerprints": before,
    }


# --- phase 2: the migration ------------------------------------------------


def phase2_migrate(state: dict[str, object]) -> dict[str, object]:
    say()
    say("== Phase 2: run 0006 -> 0007 on the rehearsal clone only ==")
    target = REHEARSAL.resolve()
    if target == SOURCE.resolve():
        raise SystemExit("REFUSING: the migration target is production")
    if target.parent != STAGING_DIR.resolve():
        raise SystemExit(f"REFUSING: {target} is not inside {STAGING_DIR}")
    say(f"  target      : {target}")
    say(f"  revision    : {FROM_REVISION} -> {TO_REVISION} (explicit, not 'head')")

    env = dict(os.environ)
    env["VOCAB_DATA_DIR"] = str(STAGING_DIR)
    env["VOCAB_REAL_DATA_DIR"] = str(STAGING_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    command = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(BACKEND / "alembic.ini"),
        "-x",
        f"db_url=sqlite:///{target.as_posix()}",
        "upgrade",
        TO_REVISION,
    ]
    say(f"  command     : {' '.join(command)}")
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    elapsed = time.perf_counter() - started
    say(f"  exit code   : {result.returncode}")
    say(f"  elapsed     : {elapsed:.2f}s")
    say("  --- stdout ---")
    for line in (result.stdout or "").strip().splitlines():
        say(f"  {line}")
    say("  --- stderr ---")
    for line in (result.stderr or "").strip().splitlines():
        say(f"  {line}")

    connection = connect(REHEARSAL, read_only=True)
    try:
        after_revision = revision(connection)
    finally:
        connection.close()
    say(f"  revision now: {after_revision}")

    check("alembic exited 0", result.returncode == 0, result.stderr)
    check("revision is now 0007", after_revision == [TO_REVISION], after_revision)
    warnings = [
        line
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
        if "warn" in line.lower()
    ]
    say(f"  warnings    : {len(warnings)}")
    for line in warnings:
        say(f"    {line}")
    return {
        "command": command,
        "exit_code": result.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "after_revision": after_revision,
        "warnings": warnings,
    }


# --- phase 3: integrity ----------------------------------------------------


def phase3_integrity(state: dict[str, object]) -> dict[str, object]:
    say()
    say("== Phase 3: data integrity ==")
    connection = connect(REHEARSAL, read_only=True)
    try:
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        fk_violations = connection.execute("pragma foreign_key_check").fetchall()
        after_tables = tables(connection)
        after = {table: row_fingerprint(connection, table) for table in after_tables}
        after_schema = schema(connection)

        check("integrity_check = ok", integrity == "ok", integrity)
        check("foreign_key_check = 0 violations", not fk_violations, fk_violations[:5])

        before: dict[str, tuple[int, str]] = state["before_fingerprints"]  # type: ignore[assignment]
        check("the same tables exist", sorted(before) == sorted(after),
              f"{sorted(set(after) - set(before))} added, {sorted(set(before) - set(after))} removed")

        # ``alembic_version`` is the one table that must change: it is the
        # migration's own bookkeeping row. Every business table must not.
        check("the only changed table is alembic_version",
              [table for table in after if before.get(table) != after[table]] == ["alembic_version"],
              [table for table in after if before.get(table) != after[table]])

        say()
        say(f"  {'table':28} {'rows before':>11} {'rows after':>10}  content")
        for table in sorted(after):
            rows_before, hash_before = before.get(table, (0, ""))
            rows_after, hash_after = after[table]
            same = (rows_before, hash_before) == (rows_after, hash_after)
            counts = "same" if rows_before == rows_after else f"{rows_before} -> {rows_after}"
            if table == "alembic_version":
                verdict = "revision advanced (expected)"
            else:
                verdict = "identical" if same else "CHANGED"
            say(
                f"  {table:28} {rows_before:>11} {rows_after:>10}  {verdict} ({counts})"
            )
            if table != "alembic_version" and not same:
                failures.append(
                    f"{table}: {rows_before} rows/{hash_before[:12]} became "
                    f"{rows_after} rows/{hash_after[:12]}"
                )
        check("every business table kept its rows and its content",
              all(before.get(t, (0, "")) == after[t] for t in after if t != "alembic_version"),
              "see the table above")

        # The spot checks the task asks for, printed so a human can read them.
        say()
        say("  spot checks (values must be identical before and after):")
        spot: dict[str, object] = {}
        source = connect(SOURCE, read_only=True)
        try:
            queries = [
                (
                    "word.source_meanings",
                    "select id, word, source_meanings, anchor, semantic_note from word order by id",
                ),
                (
                    "article.content/translation",
                    "select id, title, content, translation, translated_at from article order by id",
                ),
                (
                    "review_event.status",
                    (
                        "select id, word_id, lexicon_entry_id, timestamp, result, status_before, "
                        "status_after, review_type from review_event order by id"
                    ),
                ),
                (
                    "user_word_state.next_review_at",
                    (
                        "select id, user_id, lexicon_entry_id, status, next_review_at, last_review, "
                        "recall_success, recall_fail from user_word_state order by id"
                    ),
                ),
            ]
            for label, statement in queries:
                left = [tuple(row) for row in source.execute(statement)]
                right = [tuple(row) for row in connection.execute(statement)]
                identical = left == right
                spot[label] = {"rows": len(left), "identical": identical}
                check(f"{label} identical ({len(left)} rows)", identical,
                      "the rows differ" if not identical else "")
                for row in right[:2]:
                    say(f"      {row}")
                if len(right) > 2:
                    say(f"      ... {len(right) - 2} more row(s)")
        finally:
            source.close()
    finally:
        connection.close()
    source = connect(SOURCE, read_only=True)
    try:
        source_schema = schema(source)
    finally:
        source.close()
    return {
        "integrity_check": integrity,
        "foreign_key_check_violations": len(fk_violations),
        "after_tables": after_tables,
        "after_fingerprints": after,
        "schema_identical_to_source": after_schema == source_schema,
        "spot_checks": spot,
    }


# --- phase 4: the foreign keys --------------------------------------------


def phase4_foreign_keys(state: dict[str, object]) -> dict[str, object]:
    say()
    say("== Phase 4: physical foreign keys vs the ORM ==")
    declared = orm_foreign_keys()
    connection = connect(REHEARSAL, read_only=True)
    result: dict[str, object] = {}
    try:
        total_orm = sum(len(columns) for columns in declared.values())
        say(f"  ORM declares {total_orm} foreign keys across {len(declared)} tables")
        say()
        say(f"  {'table':26} {'column':22} ORM -> parent/rule                 physical")
        missing: list[str] = []
        mismatched: list[str] = []
        for table in sorted(declared):
            physical = physical_foreign_keys(connection, table)
            for column, expected in sorted(declared[table].items()):
                actual = physical.get(column)
                state_text = "match" if actual == expected else "MISMATCH"
                if actual is None:
                    missing.append(f"{table}.{column} -> {expected}")
                elif actual != expected:
                    mismatched.append(f"{table}.{column}: ORM {expected} vs physical {actual}")
                physical_text = f"{actual[0]}/{actual[1]}" if actual else "ABSENT"
                say(
                    f"  {table:26} {column:22} {expected[0]:<14} {expected[1]:<8} "
                    f"{physical_text:<26} {state_text}"
                )
        total_physical = sum(
            len(physical_foreign_keys(connection, table)) for table in tables(connection)
        )
        say()
        check(f"all {total_orm} ORM foreign keys exist physically", not missing, missing)
        check("no delete rule or parent disagrees", not mismatched, mismatched)
        check(f"no undeclared foreign key exists (physical total {total_physical})",
              total_physical == total_orm, f"{total_physical} physical vs {total_orm} declared")

        say()
        say("  bridge delete semantics:")
        bridges: dict[str, object] = {}
        for table, column, parent in BRIDGES:
            found = physical_foreign_keys(connection, table).get(column)
            ok = found == (parent, "SET NULL")
            bridges[f"{table}.{column}"] = {"expected": [parent, "SET NULL"], "found": found}
            check(f"{table}.{column} -> {parent} / SET NULL", ok, found)
        result = {
            "orm_foreign_keys": total_orm,
            "physical_foreign_keys": total_physical,
            "missing": missing,
            "mismatched": mismatched,
            "bridges": bridges,
        }
    finally:
        connection.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse 0006 -> 0007 on a clone")
    parser.add_argument("--json", type=Path, default=None, help="write the evidence here")
    args = parser.parse_args(argv)

    production_sha_before = sha256_file(SOURCE)

    state = phase1_clone()
    report["phase1"] = state
    report["phase2"] = phase2_migrate(state)
    report["phase3"] = phase3_integrity(state)
    report["phase4"] = phase4_foreign_keys(state)

    production_sha_after = sha256_file(SOURCE)
    say()
    say("== production must be untouched ==")
    say(f"  sha256 before: {production_sha_before}")
    say(f"  sha256 after : {production_sha_after}")
    check("production is byte-identical", production_sha_before == production_sha_after,
          "the live database changed")

    report["production"] = {
        "path": str(SOURCE),
        "sha256_before": production_sha_before,
        "sha256_after": production_sha_after,
        "untouched": production_sha_before == production_sha_after,
    }

    say()
    say("=" * 62)
    if failures:
        say(f"REHEARSAL FAILED: {len(failures)} problem(s)")
        for failure in failures:
            say("  -", failure)
    else:
        say("REHEARSAL PASSED: 0006 -> 0007 on the clone, production untouched")
    report["failures"] = failures
    report["verdict"] = "PASS" if not failures else "FAIL"

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        say(f"evidence: {args.json}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
