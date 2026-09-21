from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.helpers import review_dict, word_dict
from app.db import get_session
from app.models import ArticleWordExposure, ReviewEvent, Word

router = APIRouter(prefix="/api/words", tags=["words"])


@router.get("")
def list_words(
    search: str = "",
    status: str = "",
    view: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    query = select(Word)
    if search.strip():
        term = f"%{search.strip()}%"
        query = query.where(
            or_(Word.word.ilike(term), Word.anchor.ilike(term), Word.source_raw.ilike(term))
        )
    if status:
        query = query.where(Word.status == status)
    if view == "weak":
        query = query.where(Word.status == "weak")
    elif view == "recent":
        query = query.order_by(Word.first_seen.desc())
    elif view == "stale":
        query = query.where(
            or_(
                Word.last_review.is_(None),
                Word.last_review < datetime.now(UTC) - timedelta(days=14),
            )
        ).order_by(Word.last_review)
    else:
        query = query.order_by(Word.word)
    words = session.scalars(query.limit(limit)).all()
    return {"total": len(words), "words": [word_dict(word) for word in words]}


@router.get("/{word_id}")
def get_word(word_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    word = session.get(Word, word_id)
    if word is None:
        raise HTTPException(404, "单词不存在")
    reviews = session.scalars(
        select(ReviewEvent)
        .where(ReviewEvent.word_id == word_id)
        .order_by(ReviewEvent.timestamp.desc())
    ).all()
    exposures = session.scalars(
        select(ArticleWordExposure)
        .where(ArticleWordExposure.word_id == word_id)
        .order_by(ArticleWordExposure.last_exposed_at.desc())
    ).all()
    return {
        **word_dict(word),
        "review_history": [review_dict(item) for item in reviews],
        "article_exposures": [
            {
                "article_id": item.article_id,
                "context": item.context,
                "exposure_count": item.exposure_count,
                "first_exposed_at": item.first_exposed_at,
                "last_exposed_at": item.last_exposed_at,
            }
            for item in exposures
        ],
    }
