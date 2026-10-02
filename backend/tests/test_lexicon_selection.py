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
