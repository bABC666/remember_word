from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, SessionDep
from app.api.helpers import review_dict, word_dict_from_view
from app.models import Article, ArticleWordExposure, LexiconEntry, ReviewEvent, UserWordState
from app.services.userdata import (
    NotFoundError,
    WordView,
    load_user_word,
    load_user_word_state,
)

router = APIRouter(prefix="/api/words", tags=["words"])


def _to_view(row: tuple[UserWordState, LexiconEntry]) -> WordView:
    state, entry = row
    return WordView(state=state, entry=entry)


@router.get("")
def list_words(
    user: CurrentUser,
    session: SessionDep,
    search: str = "",
    status: str = "",
    view: str = "",
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    """List words **this user is learning**.

    A word only appears once a ``UserWordState`` exists for the caller, so two
    users sharing one public lexicon never see each other's vocabulary or
    learning state. Every filter runs in SQL; nothing is filtered in Python after
    a global fetch.
    """
    query = (
        select(UserWordState, LexiconEntry)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(UserWordState.user_id == user.id)
    )

    term = search.strip()
    if term:
        pattern = f"%{term}%"
        query = query.where(
            or_(
                LexiconEntry.word.ilike(pattern),
                LexiconEntry.default_anchor.ilike(pattern),
                LexiconEntry.source_raw.ilike(pattern),
            )
        )
    if status:
        query = query.where(UserWordState.status == status)
    if view == "weak":
        query = query.where(UserWordState.status == "weak")
    elif view == "recent":
        query = query.order_by(UserWordState.first_seen.desc())
    elif view == "stale":
        query = query.where(
            or_(
                UserWordState.last_review.is_(None),
                UserWordState.last_review < datetime.now(UTC) - timedelta(days=14),
            )
        ).order_by(UserWordState.last_review)
    else:
        query = query.order_by(LexiconEntry.word)

    rows = session.execute(query.limit(limit)).all()
    return {
        "total": len(rows),
        "words": [word_dict_from_view(_to_view(row)) for row in rows],
    }


@router.get("/{word_id}")
def get_word(word_id: int, user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """Detail for one of **this user's** words.

    The previous high-risk endpoint loaded ``Word`` by primary key and returned
    whatever it found, including another user's review history. The only lookups
    now are owner-scoped, so guessing an id yields 404.

    The id may be the legacy word id a client already knows, or the caller's own
    ``user_word_state`` id for words that have no legacy row.
    """
    try:
        view = load_user_word(session, user, word_id)
    except NotFoundError:
        view = load_user_word_state(session, user, word_id)

    reviews = session.scalars(
        select(ReviewEvent)
        .where(
            ReviewEvent.user_id == user.id,
            ReviewEvent.word_id == view.state.legacy_word_id,
        )
        .order_by(ReviewEvent.timestamp.desc())
    ).all()

    exposures = session.scalars(
        select(ArticleWordExposure)
        .join(Article, Article.id == ArticleWordExposure.article_id)
        .where(
            Article.user_id == user.id,
            ArticleWordExposure.word_id == view.state.legacy_word_id,
        )
        .order_by(ArticleWordExposure.last_exposed_at.desc())
    ).all()

    return {
        **word_dict_from_view(view),
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
