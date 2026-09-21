from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Article, ReviewEvent, Word

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


@router.get("")
def dashboard(session: Session = Depends(get_session)) -> dict[str, object]:
    today = datetime.now(UTC).date()
    start, end = _day_bounds(today)
    new_count = (
        session.scalar(
            select(func.count())
            .select_from(Word)
            .where(Word.first_seen >= start, Word.first_seen < end)
        )
        or 0
    )
    due_count = (
        session.scalar(
            select(func.count())
            .select_from(Word)
            .where(or_(Word.next_review_at <= datetime.now(UTC), Word.status == "weak"))
        )
        or 0
    )
    weak_count = (
        session.scalar(select(func.count()).select_from(Word).where(Word.status == "weak")) or 0
    )
    article = session.scalar(
        select(Article)
        .where(Article.created_at >= start, Article.created_at < end)
        .order_by(Article.created_at.desc())
    )

    activity_dates = set()
    for timestamp in session.scalars(
        select(ReviewEvent.timestamp).order_by(ReviewEvent.timestamp.desc()).limit(1000)
    ):
        activity_dates.add(timestamp.date())
    streak = 0
    cursor = today
    if cursor not in activity_dates and cursor - timedelta(days=1) in activity_dates:
        cursor -= timedelta(days=1)
    while cursor in activity_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return {
        "today_new": new_count,
        "due_reviews": due_count,
        "weak_words": weak_count,
        "reading_status": "completed"
        if article and article.completed
        else ("ready" if article else "not_generated"),
        "streak_days": streak,
    }
