from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import and_, func, or_, select

from app.api.deps import CurrentUser, SessionDep
from app.api.helpers import review_dict, word_dict_from_view
from app.models import Article, ArticleWordExposure, LexiconEntry, ReviewEvent, UserWordState
from app.services.concise_meaning import entry_short_meanings
from app.services.entry_provenance import entry_sources
from app.services.lexicon_selection import effective_lexicon_selection
from app.services.userdata import (
    NOT_FOUND_WORD,
    WordView,
    load_readable_lexicon,
    load_user_word,
    load_user_word_state,
    not_found,
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
    catalog: bool = False,
    offset: int = Query(default=0, ge=0),
    lexicon_id: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    """List words **this user is learning**.

    A word only appears once a ``UserWordState`` exists for the caller, so two
    users sharing one public lexicon never see each other's vocabulary or
    learning state. Every filter runs in SQL; nothing is filtered in Python after
    a global fetch.

    ``catalog=true`` instead browses the selected readable lexicon's complete
    content, with optional caller-owned progress, without creating learning state.
    """
    if catalog:
        return _list_catalog(user, session, search, status, view, limit, offset, lexicon_id)
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


def _entry_view(state: UserWordState | None, entry: LexiconEntry) -> WordView:
    if state is None:
        # Display defaults only: never attach this transient object to the session.
        state = UserWordState(
            lexicon_entry_id=entry.id, status="new", anchor_override="",
            semantic_note="", recall_success=0, recall_fail=0, context_exposure=0,
            possible_issue=False, notes="",
        )
    return WordView(state=state, entry=entry)


def _list_catalog(user, session, search, status, view, limit, offset, lexicon_id):
    scope = lexicon_id
    if scope is None:
        scope, _ = effective_lexicon_selection(session, user)
    if scope is None:
        return {"total": 0, "words": [], "lexicon": None}
    lexicon = load_readable_lexicon(session, user, scope)
    query = select(UserWordState, LexiconEntry).select_from(LexiconEntry).outerjoin(
        UserWordState, and_(
            UserWordState.lexicon_entry_id == LexiconEntry.id,
            UserWordState.user_id == user.id,
        ),
    ).where(LexiconEntry.lexicon_id == lexicon.id)
    if search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(or_(
            LexiconEntry.word.ilike(pattern), LexiconEntry.default_anchor.ilike(pattern),
            LexiconEntry.source_raw.ilike(pattern),
        ))
    if status in ("new", "unstudied"):
        query = query.where(or_(UserWordState.id.is_(None), UserWordState.status == "new"))
    elif status:
        query = query.where(UserWordState.status == status)
    if view == "weak":
        query = query.where(UserWordState.status == "weak")
    elif view == "stale":
        query = query.where(
            UserWordState.id.is_not(None), UserWordState.status != "new",
            or_(UserWordState.last_review.is_(None),
                UserWordState.last_review < datetime.now(UTC) - timedelta(days=14)),
        )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    if view == "recent":
        query = query.order_by(UserWordState.first_seen.desc())
    elif view == "stale":
        query = query.order_by(UserWordState.last_review)
    query = query.order_by(LexiconEntry.word, LexiconEntry.id)
    views = [_entry_view(state, entry) for state, entry in
             session.execute(query.offset(offset).limit(limit)).all()]
    short = entry_short_meanings(session, (item.entry.id for item in views))
    return {
        "total": total, "lexicon": {"id": lexicon.id, "name": lexicon.name},
        "words": [word_dict_from_view(item, short.get(item.entry.id)) for item in views],
    }


@router.get("/entry/{entry_id}")
def get_catalog_entry(entry_id: int, user: CurrentUser, session: SessionDep):
    entry = session.get(LexiconEntry, entry_id)
    if entry is None:
        raise not_found(NOT_FOUND_WORD)
    load_readable_lexicon(session, user, entry.lexicon_id)
    state = session.scalar(select(UserWordState).where(
        UserWordState.user_id == user.id, UserWordState.lexicon_entry_id == entry.id,
    ))
    return {
        **_word_detail(session, user, _entry_view(state, entry)),
        "sources": entry_sources(session, entry.id),
    }


@router.get("/state/{state_id}")
def get_word_state(
    state_id: int, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """Detail for one of this user's words, addressed by ``user_word_state.id``.

    The explicit namespace for words that have no legacy ``word`` row -- every word
    a user adds from an article. ``GET /api/words/{id}`` keeps the V1.1 meaning of
    the id and never falls back to this one.

    This route carries ``sources``: the per-field source record of the entry, with
    the rows a human adopted kept apart from the candidates they did not, and the
    link to the pinned revision each adopted row was read at. It is the entry page
    that shows sources, so only the entry detail route reads them; the legacy
    ``/api/words/{id}`` answers exactly what it answered before, and neither route
    changes ``source_raw``, ``source_meanings`` or the learning state.
    """
    view = load_user_word_state(session, user, state_id)
    return {
        **_word_detail(session, user, view),
        "sources": entry_sources(session, view.entry.id),
    }


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
    entry_id = view.entry.id

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
