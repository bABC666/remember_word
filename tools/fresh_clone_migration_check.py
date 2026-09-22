"""Fresh-clone migration check: verified V1.1 (0003) -> head, with FK assertions.

Reads the frozen, verified V1.1 source and copies it to a new staging file, then
migrates that copy to head and asserts:

* the schema reaches the code head;
* every ORM-declared foreign key physically exists with the right delete rule
  (PRAGMA foreign_key_list, not foreign_key_check -- the latter cannot see a
  constraint that was never created);
* no original content row changed;
* integrity and referential checks are clean.

Production is never opened for writing.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path("backend").resolve()))

TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("verified_db", TOOLS / "verified_db.py")
assert _spec and _spec.loader
verified_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verified_db)

SOURCE = Path("data/recovery/vocab-restored-v1.1.db").resolve()
BASELINE = Path("data/recovery/baseline.json").resolve()
STAGING_DIR = Path("data/staging").resolve()
#: Must be absolute: a relative sqlite:/// URL is resolved against the child
#: process's working directory (backend/), not the repository root.
STAGING = (STAGING_DIR / "fresh-clone-0003-to-head.db").resolve()

CONTENT_TABLES = {
    "word": (
        "id, word, phonetic, part_of_speech, source_meanings, source_raw, anchor, "
        "semantic_note, status, first_seen, last_review, next_review_at, recall_success, "
        "recall_fail, consecutive_failures, context_exposure, possible_issue, notes"
    ),
    "review_event": (
        "id, word_id, timestamp, result, source, article_id, status_before, status_after, "
        "review_type"
    ),
    "article": (
        "id, title, content, created_at, target_words, actual_used_words, completed, "
        "completed_at, translation, translated_at"
    ),
    "article_word_exposure": (
        "id, article_id, word_id, context, exposure_count, first_exposed_at, last_exposed_at"
    ),
    "article_word_lookup": (
        "id, article_id, surface, normalized_word, phonetic, part_of_speech, meaning, "
        "explanation, context, source, added_word_id, created_at"
    ),
    "import_batch": (
        "id, status, stage, provider, raw_ocr_text, error_stage, error_message, is_deleted"
    ),
    "import_image": (
        "id, batch_id, original_name, file_path, sha256, mime_type, width, height, "
        "ocr_text, error_message, is_deleted"
    ),
    "import_candidate": (
        "id, batch_id, word_id, word, phonetic, part_of_speech, source_meanings, source_raw, "
        "anchor, semantic_note, possible_issue, issue_note, selected, confirmed"
    ),
}

failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if not ok else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


def staging_env() -> dict[str, str]:
    return {
        **__import__("os").environ,
        "VOCAB_DATA_DIR": str(STAGING_DIR.resolve()),
        "VOCAB_REAL_DATA_DIR": str(STAGING_DIR.resolve()),
    }


def main() -> int:
    from app.db import code_head_revision
    from app.models import Base

    head = code_head_revision()
    print(f"code head: {head}")

    print()
    print("== 1. fresh clone from the verified V1.1 source ==")
    baseline = verified_db.load_baseline(BASELINE)
    ok, report = verified_db.compare_against_baseline(SOURCE, baseline)
    check("verified source still matches its baseline", ok, report["failures"])
    if not ok:
        return 1

    # Remove the previous clone *before* touching the source again: deleting a
    # SQLite file's -wal/-shm sidecars while any handle is still open leaves the
    # next connection unable to open the database.
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        sidecar = Path(str(STAGING) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    shutil.copy2(SOURCE, STAGING)
    print(f"  clone: {STAGING} ({STAGING.stat().st_size} bytes)")

    before = sqlite3.connect(f"file:{STAGING.as_posix()}?mode=ro", uri=True)
    try:
        baseline_rows = {
            table: before.execute(f"select {cols} from {table} order by id").fetchall()
            for table, cols in CONTENT_TABLES.items()
        }
        source_revision = before.execute("select version_num from alembic_version").fetchall()
    finally:
        before.close()
    check("clone starts at 0003", source_revision == [("0003_article_reading_tools",)],
          source_revision)

    print()
    print("== 2. migrate 0003 -> head ==")
    result = None
    for attempt in range(1, 4):
        command = [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(Path("backend/alembic.ini").resolve()),
            "-x",
            f"db_url=sqlite:///{STAGING.as_posix()}",
            "upgrade",
            "head",
        ]
        print(f"  attempt {attempt}: {' '.join(command[3:])}")
        print(f"    clone exists={STAGING.exists()} "
              f"bytes={STAGING.stat().st_size if STAGING.exists() else 0}")
        result = subprocess.run(
            command,
            cwd=str(Path("backend").resolve()),
            capture_output=True,
            text=True,
            check=False,
            env=staging_env(),
        )
        if result.returncode == 0:
            break
        # A leftover handle on the clone can make SQLite answer "unable to open
        # database file" for one attempt. Retry with a clean clone rather than
        # reporting a failure that is an artefact of the harness.
        print(f"  attempt {attempt} failed, rebuilding the clone and retrying")
        combined = (result.stdout + result.stderr).strip()
        print("    " + combined.splitlines()[-1] if combined else "    (no output)")
        if attempt < 3:
            for suffix in ("", "-wal", "-shm"):
                sidecar = Path(str(STAGING) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            shutil.copy2(SOURCE, STAGING)
    assert result is not None
    check("alembic upgrade head succeeded", result.returncode == 0,
          (result.stdout + result.stderr)[-1500:])
    if result.returncode != 0:
        return 1

    print()
    print("== 3. schema + data assertions ==")
    after = sqlite3.connect(f"file:{STAGING.as_posix()}?mode=ro", uri=True)
    try:
        revision_rows = after.execute("select version_num from alembic_version").fetchall()
        check("exactly one revision row", len(revision_rows) == 1, revision_rows)
        check("revision == code head", revision_rows[0][0] == head, revision_rows)

        for table, cols in CONTENT_TABLES.items():
            now_rows = after.execute(f"select {cols} from {table} order by id").fetchall()
            check(f"{table}: rows unchanged ({len(baseline_rows[table])})",
                  now_rows == baseline_rows[table],
                  f"{len(now_rows)} vs {len(baseline_rows[table])}")

        check("integerity_check ok",
              after.execute("pragma integrity_check").fetchone()[0] == "ok")
        check("foreign_key_check empty",
              after.execute("pragma foreign_key_check").fetchall() == [])

        print()
        print("== 4. physical foreign key matrix (PRAGMA foreign_key_list) ==")
        declared: dict[str, dict[str, tuple[str, str]]] = {}
        for table in Base.metadata.tables.values():
            for column in table.columns:
                for fk in column.foreign_keys:
                    declared.setdefault(table.name, {})[column.name] = (
                        fk.column.table.name,
                        (fk.ondelete or "NO ACTION").upper(),
                    )
        missing: list[str] = []
        mismatched: list[str] = []
        for table, columns in declared.items():
            physical = {
                row[3]: (row[2], row[6].upper())
                for row in after.execute(f'pragma foreign_key_list("{table}")')
            }
            for column, expected in sorted(columns.items()):
                actual = physical.get(column)
                if actual is None:
                    missing.append(f"{table}.{column} -> {expected[0]}/{expected[1]}")
                elif actual != expected:
                    mismatched.append(f"{table}.{column}: {expected} vs {actual}")
        check(f"all {sum(len(v) for v in declared.values())} ORM FKs exist physically",
              not missing, missing)
        check("no delete-rule mismatches", not mismatched, mismatched)

        print()
        print("== 5. bridge delete semantics (ON DELETE SET NULL) ==")
        for table, column, parent in (
            ("word", "user_id", "user"),
            ("word", "lexicon_entry_id", "lexicon_entry"),
            ("review_event", "user_id", "user"),
            ("review_event", "lexicon_entry_id", "lexicon_entry"),
            ("article", "user_id", "user"),
            ("article_word_exposure", "lexicon_entry_id", "lexicon_entry"),
            ("import_batch", "user_id", "user"),
            ("import_candidate", "lexicon_entry_id", "lexicon_entry"),
            ("history_event", "user_id", "user"),
        ):
            found = [
                (row[2], row[6].upper())
                for row in after.execute(f'pragma foreign_key_list("{table}")')
                if row[3] == column
            ]
            # Compared against the expected parent as well: a constraint that
            # points at the wrong table with the right delete rule is still wrong,
            # and this section is the one a reader trusts for delete semantics.
            check(f"{table}.{column} -> {parent} / SET NULL", found == [(parent, "SET NULL")], found)
    finally:
        after.close()

    print()
    print("=" * 60)
    if failures:
        print(f"FRESH CLONE MIGRATION FAILED: {len(failures)} problems")
        for failure in failures:
            print("  -", failure)
    else:
        print("FRESH CLONE MIGRATION VERIFIED: 0003 -> head with full FK matrix")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
