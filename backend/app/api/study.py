from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, SessionDep
from app.api.helpers import review_dict, word_dict_from_view
from app.schemas import ReviewRequest
from app.services.concise_meaning import entry_short_meanings
from app.services.entry_provenance import selected_meaning_sources
from app.services.lexicon_selection import effective_lexicon_selection
from app.services.study import (
    build_today_queue,
    record_review_for_user,
    record_review_for_user_state,
)

router = APIRouter(prefix="/api/study", tags=["study"])


@router.get("/today")
def today_queue(
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    lexicon_id: int | None = Query(default=None, ge=1),
) -> dict[str, object]:
    """Words to study now, **for this user only**.

    Ownership is enforced in the SQL ``where`` clause rather than by filtering a
    global result in Python, so another user's words can never be counted, let
    alone returned.

    Two things decide what comes back. ``limit`` caps the whole response, and the due
    and ``weak`` words take those slots first, so a review is never hidden behind new
    material. The new words are then limited by this user's daily allowance
    (``user_settings.daily_new_words``, paced per lexicon by
    ``user_lexicon.daily_new_words``) -- an allowance measured in words **studied
    today**, not per request, so refreshing the page cannot hand out more. The
    numbers behind it are reported as ``daily_new_words``; the rules and their
    boundaries are in ``docs/V1.2-PHASE2.8-E-DAILY-NEW-WORDS-DESIGN.md``.
    """
    selected_id, source = effective_lexicon_selection(session, user)
    scope = lexicon_id if lexicon_id is not None else selected_id
    queue = build_today_queue(session, user, limit=limit, lexicon_id=scope)
    # One query for the whole response. ``entry_short_meanings`` returns only
    # human-confirmed values, so a word with an unconfirmed proposal answers with an
    # empty list and the client falls back to the source meanings.
    short = entry_short_meanings(session, (view.entry.id for view in queue.words))
    attribution = selected_meaning_sources(session, [view.entry.id for view in queue.words])
    words = []
    for view in queue.words:
        word = word_dict_from_view(view, short.get(view.entry.id))
        if view.entry.lexicon.source_type == "netem":
            word["source_meaning_sources"] = attribution.get(view.entry.id, [])
        words.append(word)
    return {
        "total": len(words),
        "lexicon_id": scope,
        "selection_source": "request" if lexicon_id is not None else source,
        "words": words,
        "daily_new_words": {
            "target": queue.budget.target,
            "consumed_today": queue.budget.consumed_today,
            "remaining": queue.budget.remaining,
        },
    }


@router.post("/words/{word_id}/review")
def review_word(
    word_id: int,
    payload: ReviewRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, object]:
    """Record a review of one of **this user's** words, addressed by ``word.id``.

    This is the V1.1 route and it keeps the V1.1 meaning of the id: the legacy
    ``word.id``. It deliberately does **not** fall back to ``user_word_state.id``;
    a state id sent here is simply not found. Words with no legacy row are reviewed
    through ``POST /api/study/word-states/{state_id}/review``.

    Phase 0 flagged this endpoint as the highest-risk IDOR entry point: it took a
    bare ``word_id`` and wrote to whatever row it matched. It now resolves the id
    through the caller's own row, so guessing another user's id returns 404 and
    changes nothing.
    """
    event = record_review_for_user(
        session,
        user,
        word_id,
        payload.result,
        payload.source,
        payload.review_type,
        payload.article_id,
    )
    return review_dict(event)


@router.post("/word-states/{state_id}/review")
def review_word_state(
    state_id: int,
    payload: ReviewRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, object]:
    """Record a review addressed by this user's own ``user_word_state.id``.

    A separate route with its own namespace, for words that have no legacy ``word``
    row. Nothing is inferred: the id must be one of the caller's own learning
    states, or the answer is 404 and nothing changes.
    """
    event = record_review_for_user_state(
        session,
        user,
        state_id,
        payload.result,
        payload.source,
        payload.review_type,
        payload.article_id,
    )
    return review_dict(event)
