from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep
from app.models import Article, ReviewEvent, UserWordState

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


@router.get("")
def dashboard(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """Learning statistics for **the authenticated user only**.

    Every count is scoped by ``user_id`` inside the query. Two users sharing a
    public lexicon therefore have completely independent numbers.
    """
    today = datetime.now(UTC).date()
    start, end = _day_bounds(today)
    now = datetime.now(UTC)

    new_count = (
        session.scalar(
            select(func.count())
            .select_from(UserWordState)
            .where(
                UserWordState.user_id == user.id,
                UserWordState.first_seen >= start,
                UserWordState.first_seen < end,
            )
        )
        or 0
    )
    due_count = (
        session.scalar(
            select(func.count())
            .select_from(UserWordState)
            .where(
                UserWordState.user_id == user.id,
                or_(
                    UserWordState.next_review_at <= now,
                    UserWordState.status == "weak",
                ),
            )
        )
        or 0
    )
    weak_count = (
        session.scalar(
            select(func.count())
            .select_from(UserWordState)
            .where(UserWordState.user_id == user.id, UserWordState.status == "weak")
        )
        or 0
    )
    article = session.scalar(
        select(Article)
        .where(
            Article.user_id == user.id,
            Article.created_at >= start,
            Article.created_at < end,
        )
        .order_by(Article.created_at.desc())
    )

    activity_dates = set()
    for timestamp in session.scalars(
        select(ReviewEvent.timestamp)
        .where(ReviewEvent.user_id == user.id)
        .order_by(ReviewEvent.timestamp.desc())
        .limit(1000)
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
