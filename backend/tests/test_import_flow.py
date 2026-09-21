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
