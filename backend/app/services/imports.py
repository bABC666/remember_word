from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HistoryEvent, ImportBatch, ImportCandidate, Word
from app.services.ocr.base import OCRProvider


def process_batch_ocr(session: Session, batch: ImportBatch, provider: OCRProvider) -> None:
    """Process only unfinished active images and rebuild the batch aggregate."""
    documents: list[dict[str, object]] = []
    texts: list[str] = []
    for image in batch.images:
        if image.is_deleted:
            continue
        if image.ocr_raw_json:
            documents.append(image.ocr_raw_json)
            if image.ocr_text:
                texts.append(image.ocr_text)
            continue
        document = provider.extract(Path(image.file_path))
        image.ocr_text = document.text
        image.ocr_raw_json = document.model_dump(mode="json")
        image.error_message = ""
        documents.append(image.ocr_raw_json)
        if image.ocr_text:
            texts.append(image.ocr_text)
        session.commit()
    batch.raw_ocr_text = "\n\n".join(texts)
    batch.raw_ocr_json = {"provider": provider.name, "documents": documents}
    batch.status = "ocr_complete"
    batch.stage = "ocr"
    batch.error_stage = ""
    batch.error_message = ""
    session.commit()


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
