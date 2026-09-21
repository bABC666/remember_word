from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HistoryEvent, ImportBatch, ImportCandidate, Word


def confirm_candidates(session: Session, batch_id: int, candidate_ids: list[int]) -> list[Word]:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise LookupError("Import batch not found")
    candidates = session.scalars(
        select(ImportCandidate).where(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.id.in_(candidate_ids),
            ImportCandidate.confirmed.is_(False),
        )
    ).all()
    created: list[Word] = []
    for candidate in candidates:
        if not candidate.selected or not candidate.word.strip():
            continue
        word = Word(
            word=candidate.word.strip(),
            phonetic=candidate.phonetic,
            part_of_speech=candidate.part_of_speech,
            source_meanings=list(candidate.source_meanings or []),
            source_raw=candidate.source_raw,
            anchor=candidate.anchor,
            semantic_note=candidate.semantic_note,
            possible_issue=candidate.possible_issue,
        )
        session.add(word)
        session.flush()
        candidate.confirmed = True
        candidate.word_id = word.id
        created.append(word)
    if created:
        batch.status = "confirmed"
        batch.stage = "confirmed"
        session.add(
            HistoryEvent(
                event_type="import_confirmed",
                entity_type="import_batch",
                entity_id=batch.id,
                payload={"word_ids": [word.id for word in created]},
            )
        )
    session.commit()
    return created


def record_import_failure(
    session: Session, batch_id: int, stage: str, error: Exception
) -> ImportBatch:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise LookupError("Import batch not found")
    batch.status = f"{stage}_failed"
    batch.error_stage = stage
    batch.error_message = str(error)[:2000]
    session.commit()
    session.refresh(batch)
    return batch
