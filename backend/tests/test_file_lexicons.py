import hashlib
from io import BytesIO

from sqlalchemy import select


def _file(name: str, content: str):
    return {"file": (name, BytesIO(content.encode("utf-8")), "text/plain")}


def _import(client, name: str, filename: str, content: str):
    preview = client.post("/api/lexicons/file-preview", files=_file(filename, content))
    assert preview.status_code == 200, preview.text
    return client.post(
        "/api/lexicons/file-import",
        data={"name": name, "preview_sha256": preview.json()["sha256"]},
        files=_file(filename, content),
    )


def test_preview_classifies_rows_without_writing(world):
    response = world.client.post(
        "/api/lexicons/file-preview",
        files=_file("words.txt", "Apple\napple\nwrong word!\nbanana\n"),
    )
    assert response.status_code == 200
    assert [row["status"] for row in response.json()["rows"]] == [
        "valid", "duplicate", "error", "valid"
    ]
    assert response.json()["counts"] == {"valid": 2, "duplicate": 1, "error": 1}
    assert all(item["source_type"] != "user_file" for item in world.client.get("/api/lexicons").json())


def test_import_rejects_content_changed_after_preview(world):
    original = b"apple\n"
    preview = world.client.post(
        "/api/lexicons/file-preview", files={"file": ("words.txt", BytesIO(original))},
    )
    assert preview.status_code == 200
    assert preview.json()["sha256"] == hashlib.sha256(b"words.txt\0" + original).hexdigest()
    changed = world.client.post(
        "/api/lexicons/file-import",
        data={"name": "确认过的清单", "preview_sha256": preview.json()["sha256"]},
        files=_file("words.txt", "banana\n"),
    )
    assert changed.status_code == 409
    assert all(item["name"] != "确认过的清单" for item in world.client.get("/api/lexicons").json())


def test_import_requires_preview_digest(world):
    response = world.client.post(
        "/api/lexicons/file-import", data={"name": "未预览"},
        files=_file("words.txt", "apple\n"),
    )
    assert response.status_code == 400
    assert all(item["name"] != "未预览" for item in world.client.get("/api/lexicons").json())


def test_unstudied_import_can_be_deleted_after_opening_study(world):
    response = _import(world.client, "尚未学习", "words.txt", "apple\nbanana\n")
    assert response.status_code == 201
    lexicon_id = response.json()["id"]
    assert world.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    assert world.client.get("/api/study/today").status_code == 200
    assert world.client.delete(f"/api/lexicons/{lexicon_id}").status_code == 200
    assert world.client.get(f"/api/lexicons/{lexicon_id}").status_code == 404
    assert world.client.get("/api/lexicons/selection").json()["lexicon_id"] is None


def test_import_with_real_review_progress_cannot_be_deleted(world):
    response = _import(world.client, "已学习", "words.txt", "apple\n")
    lexicon_id = response.json()["id"]
    state = world.client.get("/api/study/today", params={"lexicon_id": lexicon_id}).json()["words"][0]
    assert world.client.post(
        f'/api/study/word-states/{state["word_state_id"]}/review',
        json={"result": "know"},
    ).status_code == 200
    assert world.client.delete(f"/api/lexicons/{lexicon_id}").status_code == 409
    assert world.client.get(f"/api/lexicons/{lexicon_id}").status_code == 200


def test_unreviewed_word_with_saved_note_cannot_be_deleted(world):
    from app.models import UserWordState

    response = _import(world.client, "有笔记", "words.txt", "apple\n")
    lexicon_id = response.json()["id"]
    state_id = world.client.get(
        "/api/study/today", params={"lexicon_id": lexicon_id}
    ).json()["words"][0]["word_state_id"]
    with world.session() as session:
        session.get(UserWordState, state_id).notes = "我的例句"
        session.commit()
    assert world.client.delete(f"/api/lexicons/{lexicon_id}").status_code == 409


def test_dashboard_new_count_matches_queue_allowance_after_large_import(world):
    from app.models import UserSettings, UserWordState

    with world.session() as session:
        session.get(UserSettings, world.user_id).daily_new_words = 3
        session.commit()
    words = "\n".join(
        f"word{chr(97 + i // 676)}{chr(97 + (i // 26) % 26)}{chr(97 + i % 26)}"
        for i in range(10000)
    ) + "\n"
    response = _import(world.client, "大量单词", "words.txt", words)
    assert response.status_code == 201
    lexicon_id = response.json()["id"]
    with world.session() as session:
        assert session.query(UserWordState).filter_by(user_id=world.user_id).count() == 0
    assert world.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    queue = world.client.get("/api/study/today").json()
    assert len(queue["words"]) == 3
    assert world.client.get("/api/dashboard").json()["today_new"] == 3
    assert world.client.post(
        f'/api/study/word-states/{queue["words"][0]["word_state_id"]}/review',
        json={"result": "know"},
    ).status_code == 200
    assert world.client.get("/api/dashboard").json()["today_new"] == 2
    assert sum(word["status"] == "new" for word in world.client.get("/api/study/today").json()["words"]) == 2


def test_csv_import_keeps_order_and_private_progress(two_worlds):
    from app.models import LexiconEntry, UserWordState

    a, b = two_worlds
    content = "word,meaning,part_of_speech\nBanana,香蕉,n.\nApple,苹果,n.\nbanana,重复,n.\n"
    response = _import(a.client, "我的清单", "list.csv", content)
    assert response.status_code == 201
    lexicon_id = response.json()["id"]
    assert response.json()["imported_count"] == 2
    assert b.client.get(f"/api/lexicons/{lexicon_id}").status_code == 404
    with a.session() as session:
        entries = session.scalars(select(LexiconEntry).where(
            LexiconEntry.lexicon_id == lexicon_id
        ).order_by(LexiconEntry.sequence)).all()
        assert [(row.word, row.sequence, row.source_meanings) for row in entries] == [
            ("Banana", 1, ["香蕉"]), ("Apple", 2, ["苹果"])
        ]
        assert session.scalar(select(UserWordState).where(
            UserWordState.user_id == b.user_id,
            UserWordState.lexicon_entry_id == entries[0].id,
        )) is None
    queue = a.client.get("/api/study/today", params={"lexicon_id": lexicon_id}).json()
    assert [word["word"] for word in queue["words"]] == ["Banana", "Apple"]
    assert all(word["meaning_origin"] == "user_provided" for word in queue["words"])


def test_invalid_file_and_empty_import_do_not_create_lexicon(world):
    assert world.client.post("/api/lexicons/file-preview", files=_file("x.pdf", "apple")).status_code == 400
    preview = world.client.post(
        "/api/lexicons/file-preview", files=_file("x.txt", "wrong word!\n"),
    )
    assert preview.status_code == 200
    response = world.client.post(
        "/api/lexicons/file-import", data={"name": "空", "preview_sha256": preview.json()["sha256"]},
        files=_file("x.txt", "wrong word!\n"),
    )
    assert response.status_code == 400
    assert all(item["name"] != "空" for item in world.client.get("/api/lexicons").json())


def test_preview_rejects_phrase_in_one_word_row(world):
    response = world.client.post(
        "/api/lexicons/file-preview",
        files=_file("words.txt", "two words\n"),
    )
    assert response.status_code == 200
    assert response.json()["counts"]["error"] == 1


def test_same_word_in_two_lists_keeps_separate_progress(world):
    ids = []
    for name in ("甲", "乙"):
        response = _import(world.client, name, "list.txt", "apple\n")
        assert response.status_code == 201
        ids.append(response.json()["id"])
    first = world.client.get("/api/study/today", params={"lexicon_id": ids[0]}).json()["words"][0]
    second = world.client.get("/api/study/today", params={"lexicon_id": ids[1]}).json()["words"][0]
    assert first["word_state_id"] != second["word_state_id"]
    assert world.client.post(
        f'/api/study/word-states/{first["word_state_id"]}/review',
        json={"result": "know"},
    ).status_code == 200
    again = world.client.get("/api/study/today", params={"lexicon_id": ids[1]}).json()["words"][0]
    assert again["status"] == "new"


def test_foreign_lexicon_cannot_be_selected(two_worlds):
    a, b = two_worlds
    response = _import(a.client, "私有", "list.txt", "apple\n")
    assert b.client.get("/api/study/today", params={"lexicon_id": response.json()["id"]}).status_code == 404
