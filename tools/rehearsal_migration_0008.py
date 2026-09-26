"""Rehearse the 0007 -> 0008 migration on a disposable clone of production.

``0008_public_lexicon_import`` only *adds* four tables, which makes it far less
dangerous than ``0007`` -- but "only adds" is exactly the claim that has to be
proved against real data rather than asserted from the migration source. The test
suite cannot prove it: pytest databases are empty or built from synthetic rows, and
the whole risk here is what a migration does to the *shape production actually has*
-- 0007, 18 tables, the real 19 lexicon entries, the real indexes and foreign keys.

What this does, and nothing else:

1. opens ``data/vocab.db`` **read-only** and copies it with the SQLite **online
   backup API**. A plain file copy is not an option: production runs in WAL mode and
   its ``-wal`` file holds committed pages that a byte copy silently drops;
2. proves the clone is logically identical to production -- same revision, same
   integrity, same tables, and the same per-row hashes for every table;
3. captures a **pre-migration baseline** of the clone with the repository's own
   verifier (``tools/verified_db.py``), which is what "row counts and key row
   fingerprints" means here;
4. runs ``alembic upgrade 0008_public_lexicon_import`` -- an **explicit revision,
   never ``head``** -- against that file only, in a subprocess whose ``-x db_url``
   is absolute;
5. verifies the migration: the four new tables exist and are empty, the foreign-key
   count grew by exactly the seven the migration adds with the right delete rules,
   integrity is still ``ok``, and **every pre-existing table is unchanged** -- same
   rows, same row hashes;
6. re-validates the clone against the *project* baseline, which answers the release
   question directly: does a migrated production database still satisfy the
   verified-backup gate, or does the baseline have to be re-recorded?
7. runs ``alembic downgrade 0007_bridge_foreign_keys`` on the same clone and proves
   the four tables are gone and every pre-existing table is *still* unchanged;
8. checks that production itself did not move: same main-file SHA-256 and size as
   before the run.

Production is only ever opened read-only, and the clone lives in ``data/staging/``,
which is the one place a destructive or schema-changing operation is allowed to run.
The clone is disposable and is recreated on every run.

Usage::

    python tools/rehearsal_migration_0008.py
    python tools/rehearsal_migration_0008.py --json data/recovery/rehearsal-0008.json
    # rehearsing against another checkout's production database:
    python tools/rehearsal_migration_0008.py --source D:/other/data/vocab.db
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND = PROJECT_ROOT / "backend"
SOURCE = PROJECT_ROOT / "data" / "vocab.db"
STAGING_DIR = PROJECT_ROOT / "data" / "staging"
CLONE = STAGING_DIR / "migration-rehearsal-0007.db"
PROJECT_BASELINE = PROJECT_ROOT / "data" / "recovery" / "baseline.json"

FROM_REVISION = "0007_bridge_foreign_keys"
TO_REVISION = "0008_public_lexicon_import"

#: The four tables 0008 creates, and the foreign keys it adds: (table, column,
#: parent, on-delete). The delete rules matter as much as the count -- `SET NULL`
#: preserves provenance when the row it describes disappears, `RESTRICT` refuses to
#: remove something a run still depends on.
NEW_TABLES = (
    "source_artifact",
    "public_import_run",
    "public_import_run_source",
    "entry_source_evidence",
)
NEW_FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("public_import_run", "target_lexicon_id", "lexicon", "RESTRICT"),
    ("public_import_run", "confirmed_by_user_id", "user", "SET NULL"),
    ("public_import_run_source", "import_run_id", "public_import_run", "RESTRICT"),
    ("public_import_run_source", "source_artifact_id", "source_artifact", "RESTRICT"),
    ("entry_source_evidence", "lexicon_entry_id", "lexicon_entry", "SET NULL"),
    ("entry_source_evidence", "source_artifact_id", "source_artifact", "RESTRICT"),
    ("entry_source_evidence", "import_run_id", "public_import_run", "RESTRICT"),
)

_spec = importlib.util.spec_from_file_location(
    "verified_db", Path(__file__).resolve().parent / "verified_db.py"
)
assert _spec and _spec.loader
verified_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verified_db)

_failures: list[str] = []


def say(message: str = "") -> None:
    print(message, flush=True)


def check(label: str, ok: bool, detail: object = None) -> bool:
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {label}"
    if detail is not None:
        line += f"  ({detail})"
    say(line)
    if not ok:
        _failures.append(label)
    return ok


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def revision(connection: sqlite3.Connection) -> list[str]:
    try:
        rows = connection.execute("select version_num from alembic_version").fetchall()
    except sqlite3.Error:
        return []
    return [row[0] for row in rows]


def table_names(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%' order by name"
        )
    ]


def foreign_keys(connection: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str]]:
    """(table, column) -> (parent table, on-delete rule) for every physical key."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for table in table_names(connection):
        for row in connection.execute(f'pragma foreign_key_list("{table}")'):
            found[(table, row["from"] if isinstance(row, sqlite3.Row) else row[3])] = (
                row["table"] if isinstance(row, sqlite3.Row) else row[2],
                (row["on_delete"] if isinstance(row, sqlite3.Row) else row[6]).upper(),
            )
    return found


def source_snapshot(source: Path) -> dict[str, object]:
    """Read-only fingerprint of production, taken before anything else happens."""
    stat = source.stat()
    wal = Path(str(source) + "-wal")
    connection = connect_readonly(source)
    try:
        return {
            "path": str(source),
            "sha256": sha256_file(source),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "wal_bytes": wal.stat().st_size if wal.exists() else 0,
            "revision": revision(connection),
            "integrity_check": connection.execute("pragma integrity_check").fetchone()[0],
        }
    finally:
        connection.close()


# --- phase 1: the clone ------------------------------------------------------


def phase1_clone(source: Path, clone: Path) -> dict[str, object]:
    say()
    say("== Phase 1: open production read-only and clone it with the online backup API ==")
    if source.resolve() == clone.resolve():
        raise SystemExit("REFUSING: the clone path is the production database")
    if clone.parent.name != "staging":
        raise SystemExit(f"REFUSING: {clone} is not inside a staging directory")
    if not source.exists():
        raise SystemExit(f"production database not found: {source}")

    before = source_snapshot(source)
    say(f"  source      : {source}")
    say(f"  sha256      : {before['sha256']}")
    say(f"  bytes       : {before['bytes']}")
    say(f"  wal bytes   : {before['wal_bytes']}")
    say(f"  revision    : {before['revision']}")
    if before["wal_bytes"]:
        say("  note        : the WAL holds committed pages, so the copy uses the")
        say("                online backup API rather than a byte copy")

    clone.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        sidecar = Path(str(clone) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    with contextlib.closing(connect_readonly(source)) as origin, contextlib.closing(
        sqlite3.connect(str(clone))
    ) as target:
        origin.backup(target)
    say(f"  clone       : {clone} ({clone.stat().st_size} bytes)")

    # Logical identity, which is what matters: the backup API legitimately lays the
    # pages out differently, so a byte comparison would fail for the wrong reason.
    source_baseline = verified_db.capture_baseline(
        source, label="production, read-only, before the rehearsal"
    )
    clone_baseline = verified_db.capture_baseline(
        clone, label="rehearsal clone before 0008"
    )
    check("clone revision equals production", before["revision"] == [FROM_REVISION],
          before["revision"])
    check("clone integrity_check is ok", clone_baseline["integrity_check"] == "ok",
          clone_baseline["integrity_check"])
    check("clone has no foreign-key violations",
          clone_baseline["foreign_key_check_violations"] == 0,
          clone_baseline["foreign_key_check_violations"])
    check("clone has the same tables as production",
          set(clone_baseline["tables"]) == set(source_baseline["tables"]),
          f"{len(clone_baseline['tables'])} tables")
    mismatched = [
        name for name, info in source_baseline["tables"].items()
        if info.get("row_hashes") != clone_baseline["tables"][name].get("row_hashes")
    ]
    check("clone content is identical to production, row by row", not mismatched,
          mismatched or "every table matches")
    connection = connect_readonly(clone)
    try:
        pre_baseline = verified_db.capture_baseline(
            clone, label="staging clone before the 0008 migration"
        )
        return {
            "source": before,
            "clone_path": str(clone),
            "clone_bytes": clone.stat().st_size,
            "clone_sha256": sha256_file(clone),
            "pre_migration": pre_baseline,
            "pre_foreign_key_count": len(foreign_keys(connection)),
        }
    finally:
        connection.close()


# --- phase 2: the migration --------------------------------------------------


def run_alembic(clone: Path, *arguments: str, source_data_dir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["VOCAB_DATA_DIR"] = str(clone.parent)
    # Deliberately *not* the staging directory: a staging clone that sits inside a
    # declared real-data root counts as protected, and the downgrade phase has to be
    # allowed to run here. Declaring the production data directory instead is also
    # the honest statement of which data is real.
    env["VOCAB_REAL_DATA_DIR"] = str(source_data_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            sys.executable, "-m", "alembic",
            "-c", str(BACKEND / "alembic.ini"),
            "-x", f"db_url=sqlite:///{clone.resolve().as_posix()}",
            *arguments,
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )


def phase2_migrate(clone: Path, source_data_dir: Path) -> dict[str, object]:
    say()
    say("== Phase 2: run 0007 -> 0008 on the clone only, explicit revision ==")
    say(f"  revision    : {FROM_REVISION} -> {TO_REVISION} (explicit, not 'head')")
    started = time.perf_counter()
    result = run_alembic(clone, "upgrade", TO_REVISION, source_data_dir=source_data_dir)
    elapsed = time.perf_counter() - started
    say(f"  exit code   : {result.returncode}")
    say(f"  elapsed     : {elapsed:.2f}s")
    for line in (result.stdout or "").strip().splitlines():
        say(f"  {line}")
    for line in (result.stderr or "").strip().splitlines():
        say(f"  {line}")
    check("alembic upgrade exited 0", result.returncode == 0)
    return {
        "upgrade_exit_code": result.returncode,
        "upgrade_seconds": round(elapsed, 2),
        "upgrade_stdout": (result.stdout or "").strip(),
        "upgrade_stderr": (result.stderr or "").strip(),
    }


# --- phase 3: verification ---------------------------------------------------


def phase3_verify(state: dict[str, object], baseline_path: Path | None) -> dict[str, object]:
    say()
    say("== Phase 3: verify the migration changed nothing but the schema ==")
    clone = Path(str(state["clone_path"]))
    pre = state["pre_migration"]

    connection = connect_readonly(clone)
    try:
        connection.row_factory = sqlite3.Row
        after_revision = revision(connection)
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        fk_violations = len(connection.execute("pragma foreign_key_check").fetchall())
        tables_now = set(table_names(connection))
        present = sorted(name for name in NEW_TABLES if name in tables_now)
        counts = {
            name: connection.execute(f'select count(*) from "{name}"').fetchone()[0]
            for name in present
        }
        keys_now = foreign_keys(connection)
    finally:
        connection.close()

    post_baseline = verified_db.capture_baseline(
        clone, label="rehearsal clone after the 0008 migration"
    )
    # The repository's own comparison: fails on lost tables, lost rows, changed row
    # values, integrity problems and foreign-key violations; reports growth.
    # ``require_revision=False`` because the revision is *supposed* to have moved --
    # leaving the guard on would replace the data comparison with "the revision is
    # not 0007", which is the one difference this phase already expects.
    unchanged, report = verified_db.compare_against_baseline(
        clone, pre, require_revision=False
    )

    check("revision is now 0008", after_revision == [TO_REVISION], after_revision)
    check("integrity_check is still ok", integrity == "ok", integrity)
    check("foreign_key_check still reports zero violations", fk_violations == 0,
          fk_violations)
    check("all four new tables exist", present == sorted(NEW_TABLES),
          present)
    check("the new tables are empty", all(count == 0 for count in counts.values()),
          counts)
    expected_keys = state["pre_foreign_key_count"] + len(NEW_FOREIGN_KEYS)
    check("foreign-key count grew by exactly the seven the migration adds",
          len(keys_now) == expected_keys,
          f"{state['pre_foreign_key_count']} -> {len(keys_now)}")
    wrong_rules = [
        f"{table}.{column}"
        for table, column, parent, rule in NEW_FOREIGN_KEYS
        if keys_now.get((table, column)) != (parent, rule)
    ]
    check("every new foreign key has the intended delete rule", not wrong_rules,
          wrong_rules or "SET NULL / RESTRICT as designed")
    check("every pre-existing table is unchanged: same rows, same row hashes",
          unchanged, report["failures"] or report.get("growth") or "no changes")

    result: dict[str, object] = {
        "after_revision": after_revision,
        "after_integrity_check": integrity,
        "after_foreign_key_violations": fk_violations,
        "after_foreign_key_count": len(keys_now),
        "pre_foreign_key_count": state["pre_foreign_key_count"],
        "new_tables": present,
        "new_table_row_counts": counts,
        "compare_against_pre_migration_baseline": {
            "unchanged": unchanged,
            "failures": report["failures"],
            "growth": report["growth"],
        },
        "post_migration": post_baseline,
    }

    if baseline_path is not None and baseline_path.exists():
        say()
        say("== Phase 3b: does the recorded project baseline still validate it? ==")
        say(f"  baseline    : {baseline_path}")
        project_baseline = verified_db.load_baseline(baseline_path)
        say(f"  label       : {project_baseline.get('label')}")
        # The release question in one line: after migrating, does the gate that
        # compares production against the frozen baseline still pass, or does the
        # baseline have to be re-recorded before the release can be verified?
        still_ok, project_report = verified_db.compare_against_baseline(
            clone, project_baseline, require_revision=False
        )
        result["project_baseline"] = {
            "path": str(baseline_path),
            "label": project_baseline.get("label"),
            "baseline_revision": project_baseline.get("alembic_revision"),
            "still_validates": still_ok,
            "failures": project_report["failures"],
            "growth": project_report["growth"],
        }
        check("the recorded baseline still validates the migrated clone", still_ok,
              project_report["failures"] or "no failures")
    return result


# --- phase 4: rollback -------------------------------------------------------


def phase4_downgrade(state: dict[str, object], source_data_dir: Path) -> dict[str, object]:
    say()
    say("== Phase 4: roll the clone back to 0007 and prove it is intact ==")
    clone = Path(str(state["clone_path"]))
    result = run_alembic(
        clone, "downgrade", FROM_REVISION, source_data_dir=source_data_dir
    )
    say(f"  exit code   : {result.returncode}")
    for line in (result.stdout or "").strip().splitlines():
        say(f"  {line}")
    for line in (result.stderr or "").strip().splitlines():
        say(f"  {line}")
    check("alembic downgrade exited 0", result.returncode == 0)

    connection = connect_readonly(clone)
    try:
        connection.row_factory = sqlite3.Row
        after_revision = revision(connection)
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
        fk_violations = len(connection.execute("pragma foreign_key_check").fetchall())
        leftover = sorted(name for name in NEW_TABLES if name in set(table_names(connection)))
        key_count = len(foreign_keys(connection))
    finally:
        connection.close()

    unchanged, report = verified_db.compare_against_baseline(
        clone, state["pre_migration"]
    )
    check("revision is back to 0007", after_revision == [FROM_REVISION], after_revision)
    check("the four tables are gone", not leftover, leftover or "none left")
    check("foreign-key count is back to the pre-migration value",
          key_count == state["pre_foreign_key_count"],
          f"{state['pre_foreign_key_count']} -> {key_count}")
    check("integrity_check is ok after the downgrade", integrity == "ok", integrity)
    check("no foreign-key violations after the downgrade", fk_violations == 0,
          fk_violations)
    check("every pre-existing table survived the round trip unchanged", unchanged,
          report["failures"] or "no changes")
    return {
        "downgrade_exit_code": result.returncode,
        "after_downgrade_revision": after_revision,
        "after_downgrade_integrity_check": integrity,
        "after_downgrade_foreign_key_count": key_count,
        "after_downgrade_foreign_key_violations": fk_violations,
        "leftover_new_tables": leftover,
        "survived_round_trip": {
            "unchanged": unchanged,
            "failures": report["failures"],
        },
    }


# --- phase 5: production did not move ----------------------------------------


def phase5_source_untouched(source: Path, before: dict[str, object]) -> dict[str, object]:
    say()
    say("== Phase 5: production was only ever read ==")
    after = source_snapshot(source)
    check("production main file is byte-identical", after["sha256"] == before["sha256"],
          f"{before['sha256']} -> {after['sha256']}")
    check("production size is unchanged", after["bytes"] == before["bytes"],
          after["bytes"])
    check("production revision is unchanged", after["revision"] == before["revision"],
          after["revision"])
    if after["wal_bytes"] != before["wal_bytes"]:
        # Informational: a live instance would move the WAL on its own, and this
        # tool never writes to the main file either way.
        say(f"  note        : wal bytes moved {before['wal_bytes']} -> {after['wal_bytes']}")
    return {"after": after, "main_file_unchanged": after["sha256"] == before["sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse the 0007 -> 0008 migration on a disposable clone"
    )
    parser.add_argument("--source", type=Path, default=SOURCE,
                        help="production database to clone (opened read-only)")
    parser.add_argument("--clone", type=Path, default=CLONE,
                        help="where to write the disposable clone (must be a staging dir)")
    parser.add_argument("--baseline", type=Path, default=PROJECT_BASELINE,
                        help="recorded baseline to re-validate the migrated clone against")
    parser.add_argument("--json", type=Path, default=None,
                        help="write the evidence here (never overwritten silently)")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    clone = args.clone.resolve()
    say("Phase 2.9 rehearsal: 0007 -> 0008 on a clone of production")
    say(f"  project root: {PROJECT_ROOT}")

    state = phase1_clone(source, clone)
    state["migration"] = phase2_migrate(clone, source.parent)
    state["verification"] = phase3_verify(state, args.baseline)
    state["rollback"] = phase4_downgrade(state, source.parent)
    state["source_after"] = phase5_source_untouched(source, state["source"])

    say()
    if _failures:
        say(f"REHEARSAL FAILED: {len(_failures)} check(s) did not pass")
        for failure in _failures:
            say(f"  - {failure}")
    else:
        say("REHEARSAL PASSED: 0007 -> 0008 -> 0007 changed nothing but the schema,")
        say("and production was not written to.")

    if args.json is not None:
        payload = {
            "rehearsal": "0007_public_lexicon_import_rehearsal",
            "from_revision": FROM_REVISION,
            "to_revision": TO_REVISION,
            "passed": not _failures,
            "failures": _failures,
            "state": state,
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        if args.json.exists():
            # Evidence is never overwritten: a second attempt is a new file.
            raise SystemExit(f"REFUSING to overwrite existing evidence: {args.json}")
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        say(f"evidence    : {args.json}")

    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
