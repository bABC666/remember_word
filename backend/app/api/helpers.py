from __future__ import annotations

from app.models import Article, ImportBatch, ImportCandidate, ImportImage, ReviewEvent, Word


def word_dict(word: Word) -> dict[str, object]:
    return {
        "id": word.id,
        "word": word.word,
        "phonetic": word.phonetic,
        "part_of_speech": word.part_of_speech,
        "source_meanings": word.source_meanings,
        "source_raw": word.source_raw,
        "anchor": word.anchor,
        "semantic_note": word.semantic_note,
        "status": word.status,
        "first_seen": word.first_seen,
        "last_review": word.last_review,
        "next_review_at": word.next_review_at,
        "recall_success": word.recall_success,
        "recall_fail": word.recall_fail,
        "context_exposure": word.context_exposure,
        "possible_issue": word.possible_issue,
        "notes": word.notes,
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


def article_dict(article: Article, include_actual: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": article.id,
        "title": article.title,
        "content": article.content,
        "created_at": article.created_at,
        "target_words": article.target_words,
        "completed": article.completed,
        "completed_at": article.completed_at,
    }
    if include_actual or article.completed:
        payload["actual_used_words"] = article.actual_used_words
    return payload
