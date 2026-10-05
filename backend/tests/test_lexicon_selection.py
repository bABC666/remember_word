"""One account's explicit choice governs every default study entry."""

import sqlite3
from io import BytesIO

from sqlalchemy import select


def test_selection_migration_preserves_existing_user_settings(alembic_database):
    before = alembic_database("upgrade", "0013_concise_meaning_wikitext_binding")
    assert before.returncode == 0, before.stderr
    with sqlite3.connect(alembic_database.database) as connection:
        connection.execute("UPDATE user_settings SET daily_new_words = 23")
        connection.commit()

    after = alembic_database("upgrade", "head")
    assert after.returncode == 0, after.stderr
    with sqlite3.connect(alembic_database.database) as connection:
        row = connection.execute(
            "SELECT daily_new_words, selected_lexicon_id FROM user_settings"
        ).fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_list(user_settings)").fetchall()
    assert row == (23, None)
    assert any(key[2] == "lexicon" and key[3] == "selected_lexicon_id" for key in foreign_keys)


def _selection_downgrade_snapshot(database):
    with sqlite3.connect(database) as connection:
        tables = [
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return (
            connection.execute("SELECT version_num FROM alembic_version").fetchall(),
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall(),
            [(table, connection.execute(f'SELECT * FROM "{table}"').fetchall()) for table in tables],
        )


def test_selection_downgrade_refuses_explicit_choices_without_changes(
    alembic_database, tmp_path
):
    upgraded = alembic_database("upgrade", "0014_selected_lexicon")
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(alembic_database.database) as connection:
        admin_id = connection.execute("SELECT id FROM user WHERE role = 'admin'").fetchone()[0]
        connection.execute(
            "INSERT INTO user (username, display_name, password_hash, role, is_active, "
            "created_at, updated_at) VALUES ('other', 'Other', '!', 'user', 1, ?, ?)",
            ("2026-01-01", "2026-01-01"),
        )
        other_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        for source_type in ("downgrade-one", "downgrade-two"):
            connection.execute(
                "INSERT INTO lexicon (owner_user_id, name, description, visibility, "
                "source_type, entry_count, created_at, updated_at) "
                "VALUES (NULL, ?, '', 'public', ?, 0, ?, ?)",
                (source_type, source_type, "2026-01-01", "2026-01-01"),
            )
        lexicon_ids = [
            row[0] for row in connection.execute(
                "SELECT id FROM lexicon WHERE source_type LIKE 'downgrade-%' ORDER BY id"
            )
        ]
        connection.execute(
            "UPDATE user_settings SET daily_new_words = 23, selected_lexicon_id = ? "
            "WHERE user_id = ?", (lexicon_ids[0], admin_id),
        )
        connection.execute(
            "INSERT INTO user_settings "
            "(user_id, daily_new_words, selected_lexicon_id, created_at, updated_at) "
            "VALUES (?, 23, ?, ?, ?)",
            (other_id, lexicon_ids[1], "2026-01-01", "2026-01-01"),
        )
        connection.commit()

    before = _selection_downgrade_snapshot(alembic_database.database)
    refused = alembic_database(
        "downgrade", "0013_concise_meaning_wikitext_binding",
        extra_env={"VOCAB_REAL_DATA_DIR": str(tmp_path / "protected-real-data")},
    )
    assert refused.returncode != 0
    output = refused.stdout + refused.stderr
    assert "explicit lexicon selections would be lost" in output
    for user_id, lexicon_id in zip((admin_id, other_id), lexicon_ids):
        assert f"user_id={user_id}, selected_lexicon_id={lexicon_id}" in output
    assert _selection_downgrade_snapshot(alembic_database.database) == before


def test_selection_downgrade_allows_null_choices(alembic_database, tmp_path):
    upgraded = alembic_database("upgrade", "0014_selected_lexicon")
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(alembic_database.database) as connection:
        admin_id = connection.execute("SELECT id FROM user WHERE role = 'admin'").fetchone()[0]
        connection.execute(
            "UPDATE user_settings SET daily_new_words = 23, selected_lexicon_id = NULL "
            "WHERE user_id = ?", (admin_id,),
        )
        connection.commit()

    downgraded = alembic_database(
        "downgrade", "0013_concise_meaning_wikitext_binding",
        extra_env={"VOCAB_REAL_DATA_DIR": str(tmp_path / "protected-real-data")},
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(alembic_database.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0013_concise_meaning_wikitext_binding",
        )
        columns = [row[1] for row in connection.execute("PRAGMA table_info(user_settings)")]
        assert "selected_lexicon_id" not in columns
        assert all(
            row[3] != "selected_lexicon_id"
            for row in connection.execute("PRAGMA foreign_key_list(user_settings)")
        )
        assert connection.execute(
            "SELECT daily_new_words FROM user_settings WHERE user_id = ?", (admin_id,)
        ).fetchone() == (23,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def _import(world, name: str, word: str) -> int:
    content = (word + "\n").encode()
    preview = world.client.post(
        "/api/lexicons/file-preview",
        files={"file": ("words.txt", BytesIO(content), "text/plain")},
    )
    assert preview.status_code == 200, preview.text
    response = world.client.post(
        "/api/lexicons/file-import",
        data={"name": name, "preview_sha256": preview.json()["sha256"]},
        files={"file": ("words.txt", BytesIO(content), "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_explicit_choice_scopes_default_queue_and_keeps_progress(world):
    from app.models import UserWordState

    first = _import(world, "A", "apple")
    second = _import(world, "B", "banana")
    assert world.client.post(f"/api/lexicons/{first}/select").status_code == 200
    assert world.client.get("/api/lexicons/selection").json()["lexicon_id"] == first
    queue = world.client.get("/api/study/today").json()["words"]
    assert [item["word"] for item in queue] == ["apple"]
    state_id = queue[0]["word_state_id"]
    assert world.client.post(
        f"/api/study/word-states/{state_id}/review", json={"result": "know"}
    ).status_code == 200
    assert world.client.post(f"/api/lexicons/{second}/select").status_code == 200
    assert [item["word"] for item in world.client.get("/api/study/today").json()["words"]] == ["banana"]
    with world.session() as session:
        assert session.scalar(select(UserWordState).where(UserWordState.id == state_id)).status != "new"
    assert world.client.post(f"/api/lexicons/{first}/select").status_code == 200
    assert world.client.get("/api/lexicons/selection").json()["lexicon_id"] == first


def test_another_user_cannot_select_private_lexicon(two_worlds):
    a, b = two_worlds
    lexicon_id = _import(a, "A", "apple")
    assert b.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 404
    assert b.client.get("/api/lexicons/selection").json()["lexicon_id"] is None


def test_dashboard_word_counts_follow_current_lexicon(world):
    first = _import(world, "A", "apple")
    _import(world, "B", "banana")
    assert world.client.post(f"/api/lexicons/{first}/select").status_code == 200
    assert world.client.get("/api/dashboard").json()["today_new"] == 1


def test_netem_is_default_only_without_explicit_choice(world):
    from app.models import Lexicon, LexiconEntry, UserSettings

    own = _import(world, "自有", "banana")
    with world.session() as session:
        netem = Lexicon(name="NETEM 考研词库", visibility="public", source_type="kaoyan")
        session.add(netem)
        session.flush()
        session.add(LexiconEntry(
            lexicon_id=netem.id, word="apple", normalized_word="apple", sequence=1,
        ))
        session.commit()
        netem_id = netem.id
    try:
        choice = world.client.get("/api/lexicons/selection").json()
        assert choice == {"lexicon_id": netem_id, "source": "recommended"}
        assert [word["word"] for word in world.client.get("/api/study/today").json()["words"]] == ["apple"]
        with world.session() as session:
            assert session.get(UserSettings, world.user_id).selected_lexicon_id is None
        assert world.client.post(f"/api/lexicons/{own}/select").status_code == 200
        assert world.client.get("/api/lexicons/selection").json() == {
            "lexicon_id": own, "source": "explicit"
        }
        assert [word["word"] for word in world.client.get("/api/study/today").json()["words"]] == ["banana"]
    finally:
        with world.session() as session:
            session.delete(session.get(Lexicon, netem_id))
            session.commit()


def test_withdrawn_public_library_leaves_progress_but_not_fallback_queue(world):
    from app.models import Lexicon, LexiconEntry, UserSettings, UserWordState

    own = _import(world, "自有", "banana")
    with world.session() as session:
        library = Lexicon(name="NETEM", visibility="public", source_type="netem")
        session.add(library)
        session.flush()
        session.add(LexiconEntry(lexicon_id=library.id, word="apple", normalized_word="apple", sequence=1))
        session.commit()
        target = library.id
    assert world.client.post(f"/api/lexicons/{target}/select").status_code == 200
    queue = world.client.get("/api/study/today").json()["words"]
    state_id = queue[0]["word_state_id"]
    with world.session() as session:
        session.get(Lexicon, target).visibility = "private"
        session.commit()
    fallback = world.client.get("/api/study/today").json()["words"]
    assert all(word["lexicon_id"] != target for word in fallback)
    assert world.client.get(f"/api/study/today?lexicon_id={target}").status_code == 404
    with world.session() as session:
        assert session.get(UserWordState, state_id) is not None
        assert session.get(UserSettings, world.user_id).selected_lexicon_id == target
    assert world.client.post(f"/api/lexicons/{own}/select").status_code == 200
