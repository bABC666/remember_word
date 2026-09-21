def test_candidate_requires_explicit_confirmation(session) -> None:
    from app.models import ImportBatch, ImportCandidate, Word
    from app.services.imports import confirm_candidates

    batch = ImportBatch(status="review", raw_ocr_text="retain v. 保留")
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
    assert session.query(Word).count() == 0

    created = confirm_candidates(session, batch.id, [batch.candidates[0].id])
    assert [word.word for word in created] == ["retain"]
    assert session.query(Word).count() == 1

    assert confirm_candidates(session, batch.id, [batch.candidates[0].id]) == []
    assert session.query(Word).count() == 1


def test_ai_learning_update_cannot_overwrite_source(session) -> None:
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


def test_import_failure_preserves_existing_ocr_and_user_edits(session) -> None:
    from app.models import ImportBatch, ImportCandidate
    from app.services.imports import record_import_failure

    batch = ImportBatch(
        status="review", raw_ocr_text="raw result", raw_ocr_json={"lines": ["raw result"]}
    )
    candidate = ImportCandidate(word="retain", source_raw="user fixed", selected=True)
    batch.candidates.append(candidate)
    session.add(batch)
    session.commit()

    record_import_failure(session, batch.id, "ai", RuntimeError("network down"))
    session.refresh(batch)
    session.refresh(candidate)
    assert batch.raw_ocr_text == "raw result"
    assert batch.raw_ocr_json == {"lines": ["raw result"]}
    assert candidate.source_raw == "user fixed"
    assert batch.status == "ai_failed"


def test_import_image_removal_is_recoverable_and_hidden(session, tmp_path) -> None:
    from app.api.helpers import batch_dict
    from app.api.imports import remove_import_image
    from app.models import ImportBatch, ImportImage

    image_path = tmp_path / "wrong-page.jpg"
    image_path.write_bytes(b"preserved source image")
    batch = ImportBatch(status="uploaded", stage="upload")
    image = ImportImage(
        original_name="wrong-page.jpg",
        file_path=str(image_path),
        sha256="a" * 64,
    )
    batch.images.append(image)
    session.add(batch)
    session.commit()

    payload = remove_import_image(batch.id, image.id, session)

    session.refresh(image)
    assert image.is_deleted is True
    assert image_path.exists()
    assert payload["images"] == []
    assert batch_dict(batch)["images"] == []


def test_import_image_cannot_be_removed_after_candidates_exist(session, tmp_path) -> None:
    import pytest
    from fastapi import HTTPException

    from app.api.imports import remove_import_image
    from app.models import ImportBatch, ImportCandidate, ImportImage

    image_path = tmp_path / "page.jpg"
    image_path.write_bytes(b"source")
    batch = ImportBatch(status="review", stage="review")
    batch.images.append(
        ImportImage(original_name="page.jpg", file_path=str(image_path), sha256="b" * 64)
    )
    batch.candidates.append(ImportCandidate(word="retain", source_raw="retain v. 保留"))
    session.add(batch)
    session.commit()

    with pytest.raises(HTTPException) as error:
        remove_import_image(batch.id, batch.images[0].id, session)

    assert error.value.status_code == 409
    assert batch.images[0].is_deleted is False


def test_abandon_import_batch_keeps_source_records(session, tmp_path) -> None:
    from app.api.imports import abandon_import
    from app.models import ImportBatch, ImportImage

    image_path = tmp_path / "page.jpg"
    image_path.write_bytes(b"source")
    batch = ImportBatch(status="uploaded", stage="upload")
    batch.images.append(
        ImportImage(original_name="page.jpg", file_path=str(image_path), sha256="c" * 64)
    )
    session.add(batch)
    session.commit()

    response = abandon_import(batch.id, session)

    session.refresh(batch)
    assert response == {"message": "导入批次已移除"}
    assert batch.is_deleted is True
    assert image_path.exists()
