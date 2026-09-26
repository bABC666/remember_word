from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, SessionDep
from app.api.helpers import review_dict, word_dict_from_view
from app.models import Article, ArticleWordExposure, LexiconEntry, ReviewEvent, UserWordState
from app.services.concise_meaning import entry_short_meanings
from app.services.userdata import WordView, load_user_word, load_user_word_state

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
    views = [_to_view(row) for row in rows]
    short = entry_short_meanings(session, (view.entry.id for view in views))
    return {
        "total": len(views),
        "words": [
            word_dict_from_view(view, short.get(view.entry.id)) for view in views
        ],
    }


@router.get("/state/{state_id}")
def get_word_state(
    state_id: int, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """Detail for one of this user's words, addressed by ``user_word_state.id``.

    The explicit namespace for words that have no legacy ``word`` row -- every word
    a user adds from an article. ``GET /api/words/{id}`` keeps the V1.1 meaning of
    the id and never falls back to this one.
    """
    return _word_detail(session, user, load_user_word_state(session, user, state_id))


@router.get("/{word_id}")
def get_word(word_id: int, user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """Detail for one of **this user's** words, addressed by legacy ``word.id``.

    The previous high-risk endpoint loaded ``Word`` by primary key and returned
    whatever it found, including another user's review history. The only lookup now
    is owner-scoped, so guessing an id yields 404.

    The id means exactly what it meant in V1.1, the legacy ``word.id``. A
    ``user_word_state.id`` sent here is not found rather than silently reinterpreted.
    """
    return _word_detail(session, user, load_user_word(session, user, word_id))


def _word_detail(session: SessionDep, user: CurrentUser, view: WordView) -> dict[str, object]:
    """The user's own learning state plus the history attached to that word.

    History is matched on ``lexicon_entry_id``, which every review and every
    exposure carries (migration 0006 backfilled the existing rows). Matching on the
    legacy ``word_id`` would compare against NULL for a word that has no legacy row,
    and ``word_id IS NULL`` matches unrelated rows instead of none.
    """
    entry_id = view.state.lexicon_entry_id

    reviews = session.scalars(
        select(ReviewEvent)
        .where(
            ReviewEvent.user_id == user.id,
            ReviewEvent.lexicon_entry_id == entry_id,
        )
        .order_by(ReviewEvent.timestamp.desc())
    ).all()

    exposures = session.scalars(
        select(ArticleWordExposure)
        .join(Article, Article.id == ArticleWordExposure.article_id)
        .where(
            Article.user_id == user.id,
            ArticleWordExposure.lexicon_entry_id == entry_id,
        )
        .order_by(ArticleWordExposure.last_exposed_at.desc())
    ).all()

    return {
        **word_dict_from_view(
            view, entry_short_meanings(session, [view.entry.id]).get(view.entry.id)
        ),
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
