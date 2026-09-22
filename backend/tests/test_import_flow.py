"""Import flow tests.

V1.2 keeps the V1.1 guarantees (candidates need explicit confirmation, source
data is never overwritten, deletions are recoverable) but every operation now
belongs to a specific user.
"""

import pytest


def test_candidate_requires_explicit_confirmation(world) -> None:
    """No candidate becomes a lexicon entry without a human confirmation."""
    from app.models import ImportBatch, ImportCandidate, LexiconEntry, UserWordState

    with world.session() as session:
        batch = ImportBatch(user_id=world.user_id, status="review", raw_ocr_text="retain v. 保留")
        batch.candidates.append(
            ImportCandidate(
                word="retain",
                source_meanings=["保留"],
                source_raw="retain v. 保留",
                anchor="保留；保持",
                selected=True,
            )
        )
        session.add(batch)
        session.commit()
        candidate_id = batch.candidates[0].id
        batch_id = batch.id

    with world.session() as session:
        assert session.query(LexiconEntry).count() == 0, "nothing may be created yet"

    with world.session() as session:
        from app.services.imports import confirm_candidates

        created = confirm_candidates(session, world.reload_user(), batch_id, [candidate_id])
        assert [entry.word for entry in created] == ["retain"]

    with world.session() as session:
        assert session.query(LexiconEntry).count() == 1
        # Confirmation creates the user's state lazily rather than a global word.
        assert session.query(UserWordState).count() == 1

    with world.session() as session:
        from app.services.imports import confirm_candidates

        again = confirm_candidates(session, world.reload_user(), batch_id, [candidate_id])
        assert again == [], "an already-confirmed candidate is not created twice"
    with world.session() as session:
        assert session.query(LexiconEntry).count() == 1


def test_confirmed_entries_land_in_the_users_own_lexicon(world, make_world) -> None:
    from app.models import ImportBatch, ImportCandidate, Lexicon

    other = make_world("import-other")
    try:
        with world.session() as session:
            batch = ImportBatch(user_id=world.user_id, status="review")
            batch.candidates.append(
                ImportCandidate(word="retain", source_raw="retain v. 保留", selected=True)
            )
            session.add(batch)
            session.commit()
            batch_id, candidate_id = batch.id, batch.candidates[0].id

        with world.session() as session:
            from app.services.imports import confirm_candidates

            created = confirm_candidates(session, world.reload_user(), batch_id, [candidate_id])
        entry = created[0]

        with world.session() as session:
            lexicon = session.get(Lexicon, entry.lexicon_id)
            assert lexicon.owner_user_id == world.user_id
            assert lexicon.visibility == "private"
    finally:
        other.client.__exit__(None, None, None)


def test_confirm_candidates_refuses_another_users_batch(world, make_world) -> None:
    """A batch id from another user must not be usable, and must look missing."""
    from app.models import ImportBatch, ImportCandidate

    other = make_world("import-victim")
    try:
        with other.session() as session:
            batch = ImportBatch(user_id=other.user_id, status="review")
            batch.candidates.append(
                ImportCandidate(word="victim", source_raw="victim", selected=True)
            )
            session.add(batch)
            session.commit()
            batch_id, candidate_id = batch.id, batch.candidates[0].id

        with world.session() as session:
            from app.services.imports import confirm_candidates

            with pytest.raises(LookupError):
                confirm_candidates(session, world.reload_user(), batch_id, [candidate_id])
    finally:
        other.client.__exit__(None, None, None)


def test_ai_learning_update_cannot_overwrite_source(session) -> None:
    """The V1.1 data principle still holds: AI cannot rewrite source fields."""
    from app.models import Word
    from app.services.words import apply_learning_update

    word = Word(word="retain", source_meanings=["原书释义"], source_raw="immutable raw")
    session.add(word)
    session.commit()
    apply_learning_update(
        session,
        word,
        {"anchor": "保留", "semantic_note": "常强调持续拥有", "source_raw": "AI overwrite"},
    )
    session.refresh(word)
    assert word.source_raw == "immutable raw"
    assert word.source_meanings == ["原书释义"]
    assert word.anchor == "保留"


def test_lexicon_content_is_not_rewritten_by_the_learning_layer(world) -> None:
    """A user's learning writes never touch the shared lexicon entry."""
    from app.models import LexiconEntry

    state_id, entry_id = world.add_word(
        "retain", anchor="保留", source_meanings=["保留", "保持"], source_raw="retain v. 保留"
    )
    with world.session() as session:
        from app.models import UserWordState

        state = session.get(UserWordState, state_id)
        state.anchor_override = "我自己的锚点"
        state.semantic_note = "我的笔记"
        session.add(state)
        session.commit()

    with world.session() as session:
        entry = session.get(LexiconEntry, entry_id)
        assert entry.default_anchor == "保留"
        assert entry.semantic_note == ""
        assert entry.source_meanings == ["保留", "保持"]
        assert entry.source_raw == "retain v. 保留"


def test_import_failure_preserves_existing_ocr_and_user_edits(world) -> None:
    from app.models import ImportBatch, ImportCandidate

    with world.session() as session:
        batch = ImportBatch(
            user_id=world.user_id,
            status="review",
            raw_ocr_text="raw result",
            raw_ocr_json={"lines": ["raw result"]},
        )
        candidate = ImportCandidate(word="retain", source_raw="user fixed", selected=True)
        batch.candidates.append(candidate)
        session.add(batch)
        session.commit()
        batch_id, candidate_id = batch.id, candidate.id

    with world.session() as session:
        from app.services.imports import record_import_failure

        record_import_failure(session, batch_id, "ai", RuntimeError("network down"))

    with world.session() as session:
        batch = session.get(ImportBatch, batch_id)
        candidate = session.get(ImportCandidate, candidate_id)
        assert batch.raw_ocr_text == "raw result"
        assert batch.raw_ocr_json == {"lines": ["raw result"]}
        assert candidate.source_raw == "user fixed"
        assert batch.status == "ai_failed"


def _batch_with_image(world, tmp_path, *, status: str = "uploaded"):
    from app.models import ImportBatch, ImportImage

    image_path = tmp_path / "wrong-page.jpg"
    image_path.write_bytes(b"preserved source image")
    with world.session() as session:
        batch = ImportBatch(user_id=world.user_id, status=status, stage="upload")
        image = ImportImage(
            original_name="wrong-page.jpg",
            file_path=str(image_path),
            sha256="a" * 64,
        )
        batch.images.append(image)
        session.add(batch)
        session.commit()
        return batch.id, image.id, image_path


def test_import_image_removal_is_recoverable_and_hidden(world, tmp_path) -> None:
    from app.api.helpers import batch_dict
    from app.models import ImportBatch, ImportImage

    batch_id, image_id, image_path = _batch_with_image(world, tmp_path)

    with world.session() as session:
        from app.api.imports import remove_import_image

        payload = remove_import_image(batch_id, image_id, world.reload_user(), session)

    assert payload["images"] == []
    assert image_path.exists(), "the original file must never be destroyed"
    with world.session() as session:
        image = session.get(ImportImage, image_id)
        batch = session.get(ImportBatch, batch_id)
        assert image.is_deleted is True
        assert batch_dict(batch)["images"] == []


def test_import_image_cannot_be_removed_after_candidates_exist(world, tmp_path) -> None:
    from fastapi import HTTPException

    from app.models import ImportBatch, ImportCandidate, ImportImage

    batch_id, image_id, _image_path = _batch_with_image(world, tmp_path, status="review")
    with world.session() as session:
        batch = session.get(ImportBatch, batch_id)
        batch.candidates.append(ImportCandidate(word="retain", source_raw="retain v. 保留"))
        session.add(batch)
        session.commit()

    with world.session() as session:
        from app.api.imports import remove_import_image

        with pytest.raises(HTTPException) as error:
            remove_import_image(batch_id, image_id, world.reload_user(), session)
    assert error.value.status_code == 409

    with world.session() as session:
        assert session.get(ImportImage, image_id).is_deleted is False


def test_abandon_import_batch_keeps_source_records(world, tmp_path) -> None:
    from app.models import ImportBatch

    batch_id, _image_id, image_path = _batch_with_image(world, tmp_path)

    with world.session() as session:
        from app.api.imports import abandon_import

        response = abandon_import(batch_id, world.reload_user(), session)

    assert response == {"message": "导入批次已移除"}
    assert image_path.exists()
    with world.session() as session:
        assert session.get(ImportBatch, batch_id).is_deleted is True


def test_another_users_import_batch_is_invisible_over_http(world, make_world) -> None:
    """Import history is personal: a guessed id answers 404, not 403."""
    other = make_world("import-secret")
    try:
        batch_id = other.add_import_batch()
        assert other.client.get(f"/api/imports/{batch_id}").status_code == 200
        assert world.client.get(f"/api/imports/{batch_id}").status_code == 404
        assert world.client.delete(f"/api/imports/{batch_id}").status_code == 404
        assert world.client.post(f"/api/imports/{batch_id}/ocr").status_code == 404
    finally:
        other.client.__exit__(None, None, None)


def test_import_list_shows_only_the_callers_batches(world, make_world) -> None:
    """The list is filtered by owner; another user's batches never appear.

    Other tests share this temporary database, so the assertion is about
    membership rather than an absolute count.
    """
    other = make_world("import-list-other")
    try:
        mine_id = world.add_import_batch()
        theirs_ids = {other.add_import_batch(), other.add_import_batch()}

        mine = {item["id"] for item in world.client.get("/api/imports").json()}
        theirs = {item["id"] for item in other.client.get("/api/imports").json()}

        assert mine_id in mine
        assert mine.isdisjoint(theirs_ids), "the caller must not see another user's batches"
        assert theirs_ids <= theirs
        assert mine.isdisjoint(theirs)
    finally:
        other.client.__exit__(None, None, None)
