from io import BytesIO

from sqlalchemy import select


def _file(name: str, content: str):
    return {"file": (name, BytesIO(content.encode("utf-8")), "text/plain")}


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


def test_csv_import_keeps_order_and_private_progress(two_worlds):
    from app.models import LexiconEntry, UserWordState

    a, b = two_worlds
    content = "word,meaning,part_of_speech\nBanana,香蕉,n.\nApple,苹果,n.\nbanana,重复,n.\n"
    response = a.client.post(
        "/api/lexicons/file-import", data={"name": "我的清单"},
        files=_file("list.csv", content),
    )
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
    response = world.client.post(
        "/api/lexicons/file-import", data={"name": "空"},
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
        response = world.client.post(
            "/api/lexicons/file-import", data={"name": name},
            files=_file("list.txt", "apple\n"),
        )
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
    response = a.client.post(
        "/api/lexicons/file-import", data={"name": "私有"},
        files=_file("list.txt", "apple\n"),
    )
    assert b.client.get("/api/study/today", params={"lexicon_id": response.json()["id"]}).status_code == 404
