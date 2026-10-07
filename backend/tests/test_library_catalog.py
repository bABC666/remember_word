from sqlalchemy import func, select

from app.models import Lexicon, LexiconEntry, ReviewEvent, UserWordState


def seed_catalog(world, count=123):
    lexicon_id = world.lexicon("NETEM")
    with world.session() as session:
        entries = [LexiconEntry(
            lexicon_id=lexicon_id, word=f"catalog-{index:03d}",
            normalized_word=f"catalog-{index:03d}", sequence=index,
            source_meanings=["目录释义"], source_raw="目录来源",
        ) for index in range(count)]
        session.add_all(entries)
        session.commit()
        ids = [entry.id for entry in entries]
    assert world.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    return lexicon_id, ids


def test_catalog_lists_all_selected_entries_with_real_total_and_pagination(world):
    lexicon_id, ids = seed_catalog(world)
    world.add_word("other-library", lexicon_id=world.lexicon("Other"))
    first = world.client.get("/api/words?catalog=true&limit=50").json()
    second = world.client.get("/api/words?catalog=true&limit=50&offset=50").json()
    last = world.client.get("/api/words?catalog=true&limit=50&offset=100").json()
    assert first["lexicon"] == {"id": lexicon_id, "name": "NETEM"}
    assert first["total"] == second["total"] == last["total"] == 123
    assert [len(page["words"]) for page in (first, second, last)] == [50, 50, 23]
    listed = [word for page in (first, second, last) for word in page["words"]]
    assert {word["lexicon_entry_id"] for word in listed} == set(ids)
    assert all(word["word_state_id"] is None for word in listed)
    assert all(word["status"] == "new" for word in listed)


def test_unstudied_filter_includes_unqueued_and_untouched_queue_entries(world):
    lexicon_id, ids = seed_catalog(world, 20)
    queued = world.client.get("/api/study/today").json()["words"]
    assert len(queued) == 15
    reviewed = queued[0]
    assert world.client.post(
        f'/api/study/word-states/{reviewed["word_state_id"]}/review',
        json={"result": "know"},
    ).status_code == 200
    payload = world.client.get("/api/words?catalog=true&status=unstudied").json()
    assert payload["total"] == 19
    assert {word["lexicon_entry_id"] for word in payload["words"]} == (
        set(ids) - {reviewed["lexicon_entry_id"]}
    )
    assert any(word["word_state_id"] is None for word in payload["words"])
    assert any(word["word_state_id"] is not None for word in payload["words"])
    searched = world.client.get("/api/words?catalog=true&search=catalog-019").json()
    assert searched["total"] == 1
    assert searched["words"][0]["lexicon_id"] == lexicon_id
    assert world.client.get("/api/words?catalog=true&status=known").json()["total"] == 0


def test_browsing_unstudied_entry_never_writes_learning_state(world):
    _, ids = seed_catalog(world, 2)
    with world.session() as session:
        before = (session.scalar(select(func.count()).select_from(UserWordState)),
                  session.scalar(select(func.count()).select_from(ReviewEvent)))
    assert world.client.get("/api/words?catalog=true").status_code == 200
    response = world.client.get(f"/api/words/entry/{ids[0]}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["word_state_id"] is None
    assert payload["first_seen"] is None
    assert payload["source_meanings"] == ["目录释义"]
    assert payload["review_history"] == payload["article_exposures"] == []
    assert "sources" in payload
    with world.session() as session:
        after = (session.scalar(select(func.count()).select_from(UserWordState)),
                 session.scalar(select(func.count()).select_from(ReviewEvent)))
    assert after == before


def test_entry_catalog_does_not_expose_another_users_private_entries(two_worlds):
    a, b = two_worlds
    lexicon_id, ids = seed_catalog(a, 2)
    assert b.client.get(f"/api/words/entry/{ids[0]}").status_code == 404
    assert b.client.get(f"/api/words?catalog=true&lexicon_id={lexicon_id}").status_code == 404


def test_shared_catalog_attaches_only_the_callers_learning_state(two_worlds):
    a, b = two_worlds
    with a.session() as session:
        lexicon = Lexicon(name="Shared catalog", visibility="public")
        session.add(lexicon)
        session.flush()
        entry = LexiconEntry(lexicon_id=lexicon.id, word="shared", normalized_word="shared")
        session.add(entry)
        session.commit()
        lexicon_id, entry_id = lexicon.id, entry.id
    assert a.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    state = a.client.get("/api/study/today").json()["words"][0]
    assert a.client.post(
        f'/api/study/word-states/{state["word_state_id"]}/review', json={"result": "know"},
    ).status_code == 200
    assert b.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    catalog = b.client.get("/api/words?catalog=true&status=unstudied").json()
    assert catalog["total"] == 1
    assert catalog["words"][0]["word_state_id"] is None
    detail = b.client.get(f"/api/words/entry/{entry_id}").json()
    assert detail["recall_success"] == 0
    assert detail["review_history"] == []
    own = a.client.get(f"/api/words/entry/{entry_id}").json()
    assert own["word_state_id"] == state["word_state_id"]
    assert len(own["review_history"]) == 1
