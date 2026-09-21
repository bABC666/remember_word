from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import ReviewEvent, Word
from app.services.scheduler import calculate_schedule


def record_review(
    session: Session,
    word_id: int,
    result: str,
    source: str,
    review_type: str,
    article_id: int | None = None,
) -> ReviewEvent:
    word = session.get(Word, word_id)
    if word is None:
        raise LookupError("Word not found")
    before = word.status
    now = datetime.now(UTC)
    schedule = calculate_schedule(before, result, word.consecutive_failures, now=now)
    event = ReviewEvent(
        word_id=word.id,
        timestamp=now,
        result=result,
        source=source,
        article_id=article_id,
        status_before=before,
        status_after=schedule.status,
        review_type=review_type,
    )
    word.status = schedule.status
    word.next_review_at = schedule.next_review_at
    word.consecutive_failures = schedule.consecutive_failures
    word.last_review = now
    if result == "know":
        word.recall_success += 1
    elif result == "fail":
        word.recall_fail += 1
    session.add(event)
    session.commit()
    session.refresh(event)
    return event
