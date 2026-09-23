"""Read-only preview contract for the proposed G6 retention policy."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import cli

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def _database(tmp_path: Path, rows: list[tuple]) -> Path:
    path = tmp_path / "preview.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE history_event (id INTEGER PRIMARY KEY, user_id INTEGER, "
            "event_type TEXT, timestamp TEXT, payload TEXT)"
        )
        connection.executemany(
            "INSERT INTO history_event VALUES (?, ?, ?, ?, ?)", rows
        )
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preview_filters_times_types_and_max_id_across_users(tmp_path):
    from app.history_retention_preview import preview_history_retention

    rows = [
        (1, 1, "login_failed", "2025-09-23 11:59:59", '{"token":"secret"}'),
        (2, 2, "reauth_failed", "2025-09-23T13:59:59+02:00", "{}"),
        (3, 1, "user_login", "2025-09-23 12:00:00", "{}"),
        (4, 2, "article_word_lookup", "2025-09-23T12:00:01Z", "{}"),
        (5, 1, "login_failed", "broken", "{}"),
        (6, 2, "login_failed", "2027-01-01T00:00:00Z", "{}"),
        (7, 1, "password_changed", "2024-01-01 00:00:00", "{}"),
        (8, 2, "article_word_lookup", "2024-01-01 00:00:00", "{}"),
        (9, 1, "user_login", "2024-01-01 00:00:00", "{}"),
    ]
    db = _database(tmp_path, rows)
    report = preview_history_retention(db, now=NOW)
    assert report["policy_status"] == "proposal_unconfirmed"
    assert report["cutoff_utc"] == "2025-09-23T12:00:00Z"
    assert report["candidate_count"] == 3
    assert report["candidate_ids_sha256"] == hashlib.sha256(b"[1,2,8]").hexdigest()
    assert report["event_counts"]["login_failed"]["candidate"] == 1
    assert report["event_counts"]["reauth_failed"]["candidate"] == 1
    assert report["event_counts"]["article_word_lookup"]["candidate"] == 1
    assert report["skipped"] == {
        "highest_id": 1,
        "not_allowlisted": 1,
        "invalid_timestamp": 1,
        "future_timestamp": 1,
        "not_before_cutoff": 2,
    }
    assert "secret" not in json.dumps(report)


def test_preview_is_repeatable_and_does_not_write_db_or_evidence(tmp_path):
    from app.history_retention_preview import preview_history_retention

    db = _database(tmp_path, [(1, 1, "login_failed", "2020-01-01", "{}")])
    baseline = tmp_path / "baseline.json"
    baseline.write_text("baseline", encoding="utf-8")
    evidence = tmp_path / "archive"
    evidence.mkdir()
    prior = evidence / "prior.json"
    prior.write_text("evidence", encoding="utf-8")
    before = {p.name: _sha(p) for p in (db, baseline, prior)}
    first = preview_history_retention(db, now=NOW)
    second = preview_history_retention(db, now=NOW)
    assert first == second
    assert first["candidate_count"] == 0  # Highest ID is always retained.
    assert before == {p.name: _sha(p) for p in (db, baseline, prior)}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["archive", "baseline.json", "preview.db"]
    assert sorted(p.name for p in evidence.iterdir()) == ["prior.json"]


def test_cli_preview_defaults_to_stdout_and_json_is_exclusive(tmp_path, monkeypatch, capsys):
    db = _database(tmp_path, [
        (1, 1, "login_failed", "2020-01-01 00:00:00", '{"password":"secret"}'),
        (2, 2, "user_login", "2020-01-01 00:00:00", "{}"),
    ])
    monkeypatch.setenv("VOCAB_DATABASE_PATH", str(db))
    monkeypatch.setattr(cli, "verify_schema_revision", lambda path: None)
    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("preview called writing settings"))
    before = _sha(db)
    assert cli.main(["history-retention", "preview"]) == 0
    output = capsys.readouterr().out
    assert "待确认" in output
    assert "secret" not in output and "password" not in output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["preview.db"]

    report_path = tmp_path / "report.json"
    assert cli.main(["history-retention", "preview", "--json", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["candidate_count"] == 1
    assert "secret" not in json.dumps(report)
    saved = _sha(report_path)
    with pytest.raises(FileExistsError):
        cli.main(["history-retention", "preview", "--json", str(report_path)])
    assert _sha(report_path) == saved
    assert _sha(db) == before
