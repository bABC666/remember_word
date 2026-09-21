from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.helpers import review_dict, word_dict
from app.db import get_session
from app.models import Word
from app.schemas import ReviewRequest
from app.services.study import record_review

router = APIRouter(prefix="/api/study", tags=["study"])


@router.get("/today")
def today_queue(
    limit: int = Query(default=50, ge=1, le=200), session: Session = Depends(get_session)
) -> dict[str, object]:
    now = datetime.now(UTC)
    words = session.scalars(
        select(Word)
        .where(
            or_(
                Word.next_review_at.is_(None),
                Word.next_review_at <= now,
                Word.status.in_(["new", "weak"]),
            )
        )
        .order_by(Word.status != "weak", Word.next_review_at, Word.first_seen)
        .limit(limit)
    ).all()
    return {"total": len(words), "words": [word_dict(word) for word in words]}


@router.post("/words/{word_id}/review")
def review_word(
    word_id: int, payload: ReviewRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    try:
        event = record_review(
            session,
            word_id,
            payload.result,
            payload.source,
            payload.review_type,
            payload.article_id,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return review_dict(event)
