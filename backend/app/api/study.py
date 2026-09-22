from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, SessionDep
from app.api.helpers import review_dict, word_dict_from_view
from app.models import LexiconEntry, UserWordState
from app.schemas import ReviewRequest
from app.services.study import record_review_for_user
from app.services.userdata import WordView

router = APIRouter(prefix="/api/study", tags=["study"])


@router.get("/today")
def today_queue(
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    """Words to study now, **for this user only**.

    Ownership is enforced in the SQL ``where`` clause rather than by filtering a
    global result in Python, so another user's words can never be counted, let
    alone returned.
    """
    now = datetime.now(UTC)
    rows = session.execute(
        select(UserWordState, LexiconEntry)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(
            UserWordState.user_id == user.id,
            or_(
                UserWordState.next_review_at.is_(None),
                UserWordState.next_review_at <= now,
                UserWordState.status.in_(["new", "weak"]),
            ),
        )
        .order_by(
            UserWordState.status != "weak",
            UserWordState.next_review_at,
            UserWordState.first_seen,
        )
        .limit(limit)
    ).all()
    words = [word_dict_from_view(WordView(state=state, entry=entry)) for state, entry in rows]
    return {"total": len(words), "words": words}


@router.post("/words/{word_id}/review")
def review_word(
    word_id: int,
    payload: ReviewRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, object]:
    """Record a review of one of **this user's** words.

    Phase 0 flagged this endpoint as the highest-risk IDOR entry point: it took a
    bare ``word_id`` and wrote to whatever row it matched. It now resolves the id
    through the caller's own ``UserWordState``, so guessing another user's id
    returns 404 and changes nothing.
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
