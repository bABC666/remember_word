"""Compare the live database against the verified recovery source.

Row counts of a *running* application legitimately grow (the app recorded a
login-free onboarding call and a history event while the acceptance check ran).
What must never change is the recovered content itself, so the live database is
compared field by field against the verified restore source.

Usage::

    python tools/compare_with_source.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SOURCE = Path("data/recovery/vocab-restored-v1.1.db")
LIVE = Path("data/vocab.db")
REPORT = Path("data/recovery/live-vs-source-comparison.json")

#: Tables whose content must be identical to the verified source.
FROZEN_TABLES = {
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
        "id, batch_id, word_id, word, phonetic, part_of_speech, source_meanings, "
        "source_raw, anchor, semantic_note, possible_issue, issue_note, selected, confirmed"
    ),
}

#: Tables allowed to grow while the app runs.
GROWABLE_TABLES = {"history_event", "app_setting"}


def rows(connection: sqlite3.Connection, table: str, columns: str) -> list[tuple]:
    return connection.execute(f"select {columns} from {table} order by id").fetchall()


def main() -> int:
    if not SOURCE.exists():
        print("verified source missing:", SOURCE)
        return 1

    source = sqlite3.connect(str(SOURCE))
    live = sqlite3.connect(str(LIVE))
    report: dict[str, object] = {"source": str(SOURCE), "live": str(LIVE), "tables": {}}
    problems: list[str] = []

    for table, columns in FROZEN_TABLES.items():
        source_rows = rows(source, table, columns)
        live_rows = rows(live, table, columns)
        identical = source_rows == live_rows
        report["tables"][table] = {
            "source_rows": len(source_rows),
            "live_rows": len(live_rows),
            "identical": identical,
        }
        print(
            "  %-24s source=%-4d live=%-4d identical=%s"
            % (table, len(source_rows), len(live_rows), identical)
        )
        if not identical:
            problems.append(f"{table} differs from the verified source")

    for table in GROWABLE_TABLES:
        source_count = source.execute(f"select count(*) from {table}").fetchone()[0]
        live_count = live.execute(f"select count(*) from {table}").fetchone()[0]
        report["tables"][table] = {
            "source_rows": source_count,
            "live_rows": live_count,
            "identical": None,
            "note": "append-only while the application runs",
        }
        print(
            "  %-24s source=%-4d live=%-4d (append-only)"
            % (table, source_count, live_count)
        )
        if live_count < source_count:
            problems.append(f"{table} lost rows: {live_count} < {source_count}")

    integrity = live.execute("pragma integrity_check").fetchone()[0]
    foreign_keys = live.execute("pragma foreign_key_check").fetchall()
    report["integrity_check"] = integrity
    report["foreign_key_violations"] = len(foreign_keys)
    print()
    print("  live integrity_check       :", integrity)
    print("  live foreign_key_check     :", len(foreign_keys), "violations")
    if integrity != "ok":
        problems.append(f"integrity_check = {integrity}")
    if foreign_keys:
        problems.append("foreign_key_check violations present")

    source.close()
    live.close()

    report["problems"] = problems
    report["verdict"] = "LIVE MATCHES VERIFIED SOURCE" if not problems else "MISMATCH"
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("VERDICT:", report["verdict"])
    for problem in problems:
        print("  PROBLEM:", problem)
    print("report :", REPORT)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
