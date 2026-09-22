from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    HistoryEvent,
    ImportBatch,
    ImportCandidate,
    Lexicon,
    LexiconEntry,
    User,
    UserLexicon,
)
from app.services.ocr.base import OCRProvider
from app.services.userdata import get_or_create_word_state

#: Imports confirmed by a user land in a lexicon that user owns, so scanning a
#: book never adds words to somebody else's vocabulary.


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


def import_lexicon(session: Session, user: User) -> Lexicon:
    """The user's private lexicon for imported books, created on first use."""
    existing = session.scalar(
        select(Lexicon)
        .where(Lexicon.owner_user_id == user.id, Lexicon.source_type == "import")
        .order_by(Lexicon.id)
        .limit(1)
    )
    if existing is not None:
        return existing

    lexicon = Lexicon(
        owner_user_id=user.id,
        name="导入词库",
        description="从图片导入并人工校对入库的单词",
        visibility="private",
        source_type="import",
    )
    session.add(lexicon)
    session.flush()
    if session.scalar(
        select(UserLexicon).where(
            UserLexicon.user_id == user.id, UserLexicon.lexicon_id == lexicon.id
        )
    ) is None:
        session.add(UserLexicon(user_id=user.id, lexicon_id=lexicon.id, enabled=True))
    return lexicon


def confirm_candidates(
    session: Session, user: User, batch_id: int, candidate_ids: list[int]
) -> list[LexiconEntry]:
    """Turn reviewed candidates into lexicon entries owned by the acting user.

    AI-produced values become lexicon content; the user's own learning state is
    created lazily, on first study, never pre-generated.
    """
    batch = session.get(ImportBatch, batch_id)
    if batch is None or batch.user_id != user.id:
        raise LookupError("Import batch not found")
    candidates = session.scalars(
        select(ImportCandidate).where(
            ImportCandidate.batch_id == batch_id,
            ImportCandidate.id.in_(candidate_ids),
            ImportCandidate.confirmed.is_(False),
        )
    ).all()
    lexicon = import_lexicon(session, user)
    created: list[LexiconEntry] = []
    for candidate in candidates:
        if not candidate.selected or not candidate.word.strip():
            continue
        normalized = candidate.word.strip().casefold()
        entry = session.scalar(
            select(LexiconEntry).where(
                LexiconEntry.lexicon_id == lexicon.id,
                LexiconEntry.normalized_word == normalized,
            )
        )
        if entry is None:
            entry = LexiconEntry(
                lexicon_id=lexicon.id,
                word=candidate.word.strip(),
                normalized_word=normalized,
                phonetic=candidate.phonetic,
                part_of_speech=candidate.part_of_speech,
                source_meanings=list(candidate.source_meanings or []),
                source_raw=candidate.source_raw,
                default_anchor=candidate.anchor,
                semantic_note=candidate.semantic_note,
                possible_issue=candidate.possible_issue,
            )
            session.add(entry)
            session.flush()
        # A newly confirmed word starts as the user's own, untouched, "new" state.
        get_or_create_word_state(session, user, entry)
        candidate.confirmed = True
        candidate.lexicon_entry_id = entry.id
        created.append(entry)

    if created:
        batch.status = "confirmed"
        batch.stage = "confirmed"
        lexicon.entry_count = (
            session.scalar(
                select(func.count())
                .select_from(LexiconEntry)
                .where(LexiconEntry.lexicon_id == lexicon.id)
            )
            or 0
        )
        session.add(
            HistoryEvent(
                user_id=user.id,
                event_type="import_confirmed",
                entity_type="import_batch",
                entity_id=batch.id,
                payload={
                    "lexicon_id": lexicon.id,
                    "lexicon_entry_ids": [entry.id for entry in created],
                },
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
