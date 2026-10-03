"""Session IDs must not acquire a previous session's baseline identity."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from tests.conftest import TEST_PASSWORD, run_alembic


def test_login_after_pruning_the_highest_session_uses_a_new_id(world) -> None:
    from app.models import UserSession
    from app.services.auth import create_session, prune_sessions

    with world.session() as session:
        _token, prior = create_session(session, world.reload_user())
        old_id = prior.id
        prior.revoked_at = datetime.now(UTC)
        session.commit()
        assert prune_sessions(session) >= 1
        assert session.get(UserSession, old_id) is None

    login = world.client.post(
        "/api/auth/login",
        json={"username": world.username, "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    with world.session() as session:
        new_id = max(row.id for row in session.query(UserSession).all())
    assert new_id > old_id


def test_0015_preserves_sessions_and_indexes_then_keeps_high_water_mark(
    tmp_path: Path,
) -> None:
    database = tmp_path / "vocab.db"
    assert run_alembic(database, "upgrade", "0014_selected_lexicon").returncode == 0

    with sqlite3.connect(database) as connection:
        user_id = connection.execute("SELECT id FROM user ORDER BY id LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO user_session (id, user_id, token_hash, created_at, "
            "expires_at, last_seen_at, revoked_at, user_agent) VALUES "
            "(7, ?, ?, '2026-01-01', '2027-01-01', NULL, NULL, 'probe')",
            (user_id, "a" * 64),
        )
        prior_row = connection.execute("SELECT * FROM user_session").fetchone()
        prior_indexes = {
            row[1]: row[2] for row in connection.execute("PRAGMA index_list(user_session)")
        }

    upgrade = run_alembic(database, "upgrade", "0015_session_autoincrement")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    with sqlite3.connect(database) as connection:
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='user_session'"
        ).fetchone()[0]
        assert "AUTOINCREMENT" in schema.upper()
        assert connection.execute("SELECT * FROM user_session").fetchone() == prior_row
        assert {
            row[1]: row[2] for row in connection.execute("PRAGMA index_list(user_session)")
        } == prior_indexes
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute("DELETE FROM user_session WHERE id=7")
        connection.execute(
            "INSERT INTO user_session (user_id, token_hash, created_at, "
            "expires_at, user_agent) VALUES "
            "(?, ?, '2026-02-01', '2027-02-01', 'probe')",
            (user_id, "b" * 64),
        )
        new_id = connection.execute("SELECT id FROM user_session").fetchone()[0]
        assert new_id > 7


def test_0015_refuses_direct_downgrade_on_a_disposable_copy(tmp_path: Path) -> None:
    database = tmp_path / "vocab.db"
    upgrade = run_alembic(database, "upgrade", "0015_session_autoincrement")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    before = database.read_bytes()
    other_data = tmp_path / "unrelated-data"
    other_data.mkdir()
    downgrade = run_alembic(
        database,
        "downgrade",
        "0014_selected_lexicon",
        extra_env={"VOCAB_REAL_DATA_DIR": str(other_data)},
    )
    assert downgrade.returncode != 0
    assert "0015 downgrade refused" in downgrade.stderr
    assert database.read_bytes() == before
