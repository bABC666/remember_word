from __future__ import annotations

from typing import Any

from app.models import (
    Article,
    ArticleWordLookup,
    ImportBatch,
    ImportCandidate,
    ImportImage,
    ReviewEvent,
)
from app.services.userdata import WordView


def word_dict_from_view(
    view: WordView, concise_meanings: list[dict[str, Any]] | None
) -> dict[str, object]:
    """Serialize lexicon content plus **this user's** learning state.

    Content comes from the shared ``LexiconEntry``; every learning field comes
    from the caller's own ``UserWordState``.

    ``id`` is the legacy ``word.id`` and nothing else, so it is ``None`` for a word
    that never had a legacy row (every word added from an article). It is not a
    stand-in for the state id: a client that needs to address such a word uses
    ``word_state_id`` with the explicit routes
    (``GET /api/words/state/{id}``, ``POST /api/study/word-states/{id}/review``).
    Reporting one field that sometimes meant the legacy id and sometimes the state
    id made the server accept either identifier on the same route, which is exactly
    the ambiguity that let a request resolve to a different row than the caller
    meant.

    ``concise_meanings`` is the short, human-confirmed display value the study page
    prefers, passed in already loaded so one response costs one query rather than one
    per word. It is deliberately a *separate* field from ``source_meanings``: the
    source default and the source's own raw line are still returned untouched, so a
    reader who doubts a short value can always go and read the source. An empty list
    means "no confirmed short meaning" and is the client's signal to fall back; it is
    never filled in from ``source_meanings`` on the server, because then a client
    could not tell a reviewed value from an unreviewed one.

    It has **no default**, and that is the point: a caller that forgets it would emit
    an empty list for a word that does have a confirmed value, which is
    indistinguishable from "nothing confirmed" and quietly turns the fallback signal
    into a lie. Every caller loads the values it is about to return.
    """
    state = view.state
    entry = view.entry
    return {
        "id": state.legacy_word_id,
        "word_state_id": state.id,
        "legacy_word_id": state.legacy_word_id,
        "lexicon_entry_id": entry.id,
        "lexicon_id": entry.lexicon_id,
        "word": entry.word,
        "phonetic": entry.phonetic,
        "part_of_speech": entry.part_of_speech,
        "source_meanings": entry.source_meanings,
        "source_raw": entry.source_raw,
        "concise_meanings": list(concise_meanings or []),
        # The user's override wins; the reviewed lexicon anchor is the fallback.
        "anchor": view.anchor,
        "semantic_note": view.semantic_note,
        "default_anchor": entry.default_anchor,
        "anchor_is_override": bool(state.anchor_override),
        "status": state.status,
        "first_seen": state.first_seen,
        "last_review": state.last_review,
        "next_review_at": state.next_review_at,
        "recall_success": state.recall_success,
        "recall_fail": state.recall_fail,
        "context_exposure": state.context_exposure,
        "possible_issue": state.possible_issue,
        "notes": state.notes,
    }


def review_dict(event: ReviewEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "word_id": event.word_id,
        "timestamp": event.timestamp,
        "result": event.result,
        "source": event.source,
        "article_id": event.article_id,
        "status_before": event.status_before,
        "status_after": event.status_after,
        "review_type": event.review_type,
    }


def candidate_dict(candidate: ImportCandidate) -> dict[str, object]:
    return {
        "id": candidate.id,
        "batch_id": candidate.batch_id,
        "word_id": candidate.word_id,
        "word": candidate.word,
        "phonetic": candidate.phonetic,
        "part_of_speech": candidate.part_of_speech,
        "source_meanings": candidate.source_meanings,
        "source_raw": candidate.source_raw,
        "anchor": candidate.anchor,
        "semantic_note": candidate.semantic_note,
        "possible_issue": candidate.possible_issue,
        "issue_note": candidate.issue_note,
        "selected": candidate.selected,
        "confirmed": candidate.confirmed,
    }


def image_dict(image: ImportImage) -> dict[str, object]:
    return {
        "id": image.id,
        "original_name": image.original_name,
        "file_path": image.file_path,
        "sha256": image.sha256,
        "mime_type": image.mime_type,
        "width": image.width,
        "height": image.height,
        "ocr_text": image.ocr_text,
        "ocr_raw_json": image.ocr_raw_json,
        "error_message": image.error_message,
        "is_deleted": image.is_deleted,
    }


def batch_dict(batch: ImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "status": batch.status,
        "stage": batch.stage,
        "provider": batch.provider,
        "raw_ocr_text": batch.raw_ocr_text,
        "raw_ocr_json": batch.raw_ocr_json,
        "error_stage": batch.error_stage,
        "error_message": batch.error_message,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "images": [image_dict(image) for image in batch.images if not image.is_deleted],
        "candidates": [candidate_dict(candidate) for candidate in batch.candidates],
    }


def lookup_dict(lookup: ArticleWordLookup) -> dict[str, object]:
    return {
        "id": lookup.id,
        "article_id": lookup.article_id,
        "surface": lookup.surface,
        "normalized_word": lookup.normalized_word,
        "phonetic": lookup.phonetic,
        "part_of_speech": lookup.part_of_speech,
        "meaning": lookup.meaning,
        "explanation": lookup.explanation,
        "context": lookup.context,
        "source": lookup.source,
        "added_word_id": lookup.added_word_id,
        "created_at": lookup.created_at,
    }


def article_dict(
    article: Article, include_actual: bool = False, include_lookups: bool = False
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": article.id,
        "title": article.title,
        "content": article.content,
        "created_at": article.created_at,
        "target_words": article.target_words,
        "completed": article.completed,
        "completed_at": article.completed_at,
        "translation": article.translation,
        "translated_at": article.translated_at,
    }
    if include_actual or article.completed:
        payload["actual_used_words"] = article.actual_used_words
    if include_lookups:
        payload["lookup_history"] = [lookup_dict(item) for item in article.word_lookups]
    return payload
