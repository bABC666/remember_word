from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep
from app.models import Article, LexiconEntry, ReviewEvent, UserWordState
from app.services.day import day_bounds
from app.services.lexicon_selection import effective_lexicon_selection
from app.services.study import build_today_queue

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """Learning statistics for **the authenticated user only**.

    Every count is scoped by ``user_id`` inside the query. Two users sharing a
    public lexicon therefore have completely independent numbers.
    """
    today = datetime.now(UTC).date()
    # One definition of "today" for the whole application -- see app.services.day;
    # the study queue's daily new-word allowance uses the same window.
    start, end = day_bounds(today)
    now = datetime.now(UTC)
    selected_id, _ = effective_lexicon_selection(session, user)
    word_scope = [] if selected_id is None else [LexiconEntry.lexicon_id == selected_id]

    # Count the new words today's study endpoint would actually offer: due
    # reviews take slots first, then the user's and lexicon's daily allowance.
    queue = build_today_queue(session, user, limit=50, lexicon_id=selected_id)
    new_count = sum(view.state.status == "new" for view in queue.words)
    due_count = (
        session.scalar(
            select(func.count())
            .select_from(UserWordState)
            .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
            .where(
                UserWordState.user_id == user.id,
                *word_scope,
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
            .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
            .where(UserWordState.user_id == user.id, *word_scope,
                   UserWordState.status == "weak")
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
