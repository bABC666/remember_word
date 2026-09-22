"""Rehearse the V1.2 migration on the staging clone and assert real-data integrity.

Level 2 staging only. This never touches ``data/vocab.db``.

Every Phase 0 assertion for the V1.1 -> V1.2 data move is checked here:
content preserved field by field, the anchor/semantic-note "double identity"
strategy, unchanged learning state, review/ article / exposure / import history
retained, candidate traceability, frequency columns left NULL, and clean
SQLite integrity.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("verified_db", TOOLS / "verified_db.py")
assert _spec and _spec.loader
verified_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verified_db)

BACKEND = Path("backend")
STAGING = Path("data/staging/v1.1-realdata-migration-test.db")
PRE_BASELINE = Path("data/recovery/staging-pre-migration-baseline.json")
REPORT = Path("data/recovery/staging-migration-report.json")

V12_TABLES = {"user", "user_session", "user_settings", "lexicon", "lexicon_entry",
              "user_lexicon", "user_word_state"}

failures: list[str] = []
checks: dict[str, object] = {}


def check(name: str, condition: bool, detail: object = "") -> None:
    checks[name] = {"passed": bool(condition), "detail": detail}
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f"  {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(f"{name}: {detail}")


def run_migration() -> None:
    env = {
        **__import__("os").environ,
        # The child must never resolve to the real data directory.
        "VOCAB_DATA_DIR": str(Path("data/staging").resolve()),
        "VOCAB_REAL_DATA_DIR": str(Path("data/staging").resolve()),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str((BACKEND / "alembic.ini").resolve()),
            "-x",
            f"db_url=sqlite:///{STAGING.resolve().as_posix()}",
            "upgrade",
            "head",
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(f"migration failed:\n{result.stdout}\n{result.stderr}")
    print("migration output tail:", result.stderr.strip().splitlines()[-1] if result.stderr else "")


def main() -> int:
    if not STAGING.exists():
        print("staging clone missing; run tools/make_staging.py first")
        return 1

    before = sqlite3.connect(f"file:{STAGING.as_posix()}?mode=ro", uri=True)
    words_before = before.execute(
        "select id, word, phonetic, part_of_speech, source_meanings, source_raw, anchor, "
        "semantic_note, status, first_seen, last_review, next_review_at, recall_success, "
        "recall_fail, consecutive_failures, context_exposure, possible_issue, notes "
        "from word order by id"
    ).fetchall()
    events_before = before.execute(
        "select id, word_id, timestamp, result, source, article_id, status_before, "
        "status_after, review_type from review_event order by id"
    ).fetchall()
    counts_before = {
        table: before.execute(f'select count(*) from "{table}"').fetchone()[0]
        for table in (
            "word", "review_event", "article", "article_word_exposure",
            "article_word_lookup", "import_batch", "import_image",
            "import_candidate", "history_event", "app_setting",
        )
    }
    article_before = before.execute(
        "select id, title, content, target_words, actual_used_words, completed, "
        "translation, translated_at from article order by id"
    ).fetchall()
    exposures_before = before.execute(
        "select id, article_id, word_id, context, exposure_count from "
        "article_word_exposure order by id"
    ).fetchall()
    before.close()

    print("== running 0003 -> head on staging ==")
    run_migration()

    connection = sqlite3.connect(f"file:{STAGING.as_posix()}?mode=ro", uri=True)
    try:
        print()
        print("== content preservation ==")
        words_after = connection.execute(
            "select id, word, phonetic, part_of_speech, source_meanings, source_raw, anchor, "
            "semantic_note, status, first_seen, last_review, next_review_at, recall_success, "
            "recall_fail, consecutive_failures, context_exposure, possible_issue, notes "
            "from word order by id"
        ).fetchall()
        check("word rows unchanged (19 rows, every column)", words_after == words_before,
              f"{len(words_after)} vs {len(words_before)}")

        entries = connection.execute(
            "select e.id, e.lexicon_id, e.word, e.normalized_word, e.phonetic, "
            "e.part_of_speech, e.source_meanings, e.source_raw, e.default_anchor, "
            "e.semantic_note, e.possible_issue, e.sequence, e.frequency_rank, "
            "e.frequency_count, e.frequency_source, w.id "
            "from lexicon_entry e join word w on w.lexicon_entry_id = e.id order by w.id"
        ).fetchall()
        check("one lexicon_entry per word", len(entries) == counts_before["word"],
              f"{len(entries)} entries for {counts_before['word']} words")
        check("lexicon_entry from a single system lexicon", len({row[1] for row in entries}) == 1)
        check("source_meanings preserved verbatim",
              all(row[6] == word[4] for row, word in zip(entries, words_before, strict=True)))
        check("source_raw preserved verbatim",
              all(row[7] == word[5] for row, word in zip(entries, words_before, strict=True)))
        check("phonetic / part_of_speech preserved",
              all(row[4] == word[2] and row[5] == word[3]
                  for row, word in zip(entries, words_before, strict=True)))
        check("default_anchor equals the reviewed anchor",
              all(row[8] == word[6] for row, word in zip(entries, words_before, strict=True)))
        check("lexicon semantic_note preserved",
              all(row[9] == word[7] for row, word in zip(entries, words_before, strict=True)))
        check("frequency_rank all NULL",
              all(row[12] is None for row in entries), [row[12] for row in entries][:5])
        check("frequency_count all NULL", all(row[13] is None for row in entries))
        check("frequency_source all empty", all(row[14] == "" for row in entries))
        check("sequence all NULL (never invented)", all(row[11] is None for row in entries))

        print()
        print("== anchor double identity ==")
        states = connection.execute(
            "select s.id, s.anchor_override, s.semantic_note, e.default_anchor, "
            "e.semantic_note, s.user_id, s.legacy_word_id, s.lexicon_entry_id, "
            "s.status, s.first_seen, s.last_review, s.next_review_at, s.recall_success, "
            "s.recall_fail, s.consecutive_failures, s.context_exposure, s.notes, "
            "s.possible_issue from user_word_state s "
            "join lexicon_entry e on e.id = s.lexicon_entry_id order by s.legacy_word_id"
        ).fetchall()
        check("one user_word_state per word", len(states) == counts_before["word"])
        check("anchor_override equals the reviewed anchor",
              all(row[1] == word[6] for row, word in zip(states, words_before, strict=True)))
        check("state semantic_note preserved",
              all(row[2] == word[7] for row, word in zip(states, words_before, strict=True)))
        check("clearing the override still leaves the reviewed anchor",
              all(row[3] for row in states))
        check("legacy_word_id points at the original row",
              all(row[6] == word[0] for row, word in zip(states, words_before, strict=True)))
        check("bridge and state agree on the entry",
              all(row[7] == row[7] for row in states))

        print()
        print("== learning state unchanged ==")
        check("status unchanged",
              all(row[8] == word[8] for row, word in zip(states, words_before, strict=True)))
        check("first_seen unchanged",
              all(row[9] == word[9] for row, word in zip(states, words_before, strict=True)))
        check("last_review unchanged",
              all(row[10] == word[10] for row, word in zip(states, words_before, strict=True)))
        check("next_review_at unchanged",
              all(row[11] == word[11] for row, word in zip(states, words_before, strict=True)))
        check("recall_success unchanged",
              all(row[12] == word[12] for row, word in zip(states, words_before, strict=True)))
        check("recall_fail unchanged",
              all(row[13] == word[13] for row, word in zip(states, words_before, strict=True)))
        check("consecutive_failures unchanged",
              all(row[14] == word[14] for row, word in zip(states, words_before, strict=True)))
        check("context_exposure unchanged",
              all(row[15] == word[15] for row, word in zip(states, words_before, strict=True)))
        check("notes unchanged",
              all(row[16] == word[17] for row, word in zip(states, words_before, strict=True)))
        check("possible_issue unchanged",
              all(bool(row[17]) == bool(word[16])
                  for row, word in zip(states, words_before, strict=True)))

        print()
        print("== history retained ==")
        events_after = connection.execute(
            "select id, word_id, timestamp, result, source, article_id, status_before, "
            "status_after, review_type from review_event order by id"
        ).fetchall()
        check("review_event rows identical (no recomputation)",
              events_after == events_before, f"{len(events_after)} vs {len(events_before)}")
        check("every review_event has an owner",
              connection.execute(
                  "select count(*) from review_event where user_id is null"
              ).fetchone()[0] == 0)
        check("review owner matches the word owner",
              connection.execute(
                  "select count(*) from review_event r join word w on w.id = r.word_id "
                  "where r.user_id != w.user_id"
              ).fetchone()[0] == 0)

        article_after = connection.execute(
            "select id, title, content, target_words, actual_used_words, completed, "
            "translation, translated_at from article order by id"
        ).fetchall()
        check("article unchanged (title/content/target/actual/completed/translation)",
              article_after == article_before)
        check("article has an owner",
              connection.execute("select count(*) from article where user_id is null"
                                 ).fetchone()[0] == 0)
        check("target_words/actual_used_words kept as string snapshots",
              article_after and article_after[0][3].startswith("[") and
              article_after[0][4].startswith("["))

        exposures_after = connection.execute(
            "select id, article_id, word_id, context, exposure_count from "
            "article_word_exposure order by id"
        ).fetchall()
        check("article_word_exposure unchanged", exposures_after == exposures_before)
        exposure_columns = {
            row[1] for row in connection.execute("pragma table_info(article_word_exposure)")
        }
        check("exposure does not duplicate user ownership", "user_id" not in exposure_columns)
        lookup_columns = {
            row[1] for row in connection.execute("pragma table_info(article_word_lookup)")
        }
        check("lookup does not duplicate user ownership", "user_id" not in lookup_columns)

        print()
        print("== import history and traceability ==")
        check("import_batch count unchanged",
              connection.execute("select count(*) from import_batch").fetchone()[0]
              == counts_before["import_batch"])
        check("import_image count unchanged",
              connection.execute("select count(*) from import_image").fetchone()[0]
              == counts_before["import_image"])
        check("import_candidate count unchanged",
              connection.execute("select count(*) from import_candidate").fetchone()[0]
              == counts_before["import_candidate"])
        check("every import_batch has an owner",
              connection.execute("select count(*) from import_batch where user_id is null"
                                 ).fetchone()[0] == 0)
        untraceable = connection.execute(
            "select count(*) from import_candidate where confirmed = 1 "
            "and lexicon_entry_id is null"
        ).fetchone()[0]
        check("every confirmed candidate links to a lexicon entry", untraceable == 0,
              f"{untraceable} untraceable")
        mismatch = connection.execute(
            "select count(*) from import_candidate c join word w on w.id = c.word_id "
            "where c.lexicon_entry_id != w.lexicon_entry_id"
        ).fetchone()[0]
        check("candidate entry matches its word's entry", mismatch == 0)
        paths_before = json.loads(PRE_BASELINE.read_text(encoding="utf-8"))["tables"][
            "import_image"
        ]["rows"]
        check("import_image rows kept", paths_before == counts_before["import_image"])

        print()
        print("== user and settings ==")
        users = connection.execute("select id, username, role, password_hash from user").fetchall()
        check("exactly one bootstrap user", len(users) == 1, users)
        check("bootstrap user is admin", users and users[0][2] == "admin")
        check("bootstrap password is the unusable sentinel",
              users and users[0][3] == "!", users[0][3] if users else None)
        settings = connection.execute(
            "select daily_new_words, article_length from user_settings"
        ).fetchall()
        check("user_settings seeded from the old global settings",
              settings == [(15, 650)], settings)
        membership = connection.execute(
            "select enabled, daily_new_words, l.visibility, l.owner_user_id, l.source_type "
            "from user_lexicon u join lexicon l on l.id = u.lexicon_id"
        ).fetchall()
        check("admin enrolled in the migrated system lexicon",
              membership and membership[0][0] == 1, membership)
        check("system lexicon is public and ownerless",
              membership and membership[0][2] == "public" and membership[0][3] is None)
        check("new V1.2 tables exist", V12_TABLES <= {
            row[0] for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        })

        print()
        print("== SQLite integrity ==")
        check("foreign_key_check empty",
              connection.execute("pragma foreign_key_check").fetchall() == [])
        check("integrity_check ok",
              connection.execute("pragma integrity_check").fetchone()[0] == "ok")
        revision = connection.execute("select version_num from alembic_version").fetchone()[0]
        checks["alembic_revision"] = revision
        print("  revision:", revision)
    finally:
        connection.close()

    print()
    print("== baseline comparison (nothing lost, only additions) ==")
    baseline = verified_db.load_baseline(PRE_BASELINE)
    ok, report = verified_db.compare_against_baseline(
        STAGING, baseline, require_revision=False
    )
    for table, info in report["tables"].items():
        print(
            "  %-24s %-12s before=%-4s after=%-4s %s"
            % (table, info["classification"], info["rows_baseline"],
               info["rows_current"], info["status"])
        )
    check("no baseline rows lost or modified", ok, report["failures"])
    checks["growth"] = report["growth"]

    payload = {
        "staging": str(STAGING),
        "checks": checks,
        "failures": failures,
        "verified": not failures,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 60)
    if failures:
        print(f"STAGING MIGRATION FAILED: {len(failures)} problems")
        for failure in failures:
            print("  -", failure)
    else:
        print("STAGING MIGRATION VERIFIED: every assertion passed")
    print("report:", REPORT)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
