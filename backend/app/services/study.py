from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import ReviewEvent, User, UserWordState
from app.services.scheduler import calculate_schedule
from app.services.userdata import find_user_word, not_found

#: The scheduling rules themselves are unchanged from V1.1. What changed in
#: V1.2 is the input: the word must belong to the acting user, which is enforced
#: by loading it through an owner-scoped query.


def record_review(
    session: Session,
    word_id: int,
    result: str,
    source: str,
    review_type: str,
    article_id: int | None = None,
) -> ReviewEvent:
    """Record a review for a legacy ``word`` row without an owner check.

    Kept for internal callers and tests that operate on a single-user database.
    HTTP endpoints must use :func:`record_review_for_user`.
    """
    state = session.query(UserWordState).filter_by(legacy_word_id=word_id).one_or_none()
    if state is None:
        raise LookupError("Word not found")
    return _apply(session, state, word_id, result, source, review_type, article_id)


def record_review_for_user(
    session: Session,
    user: User,
    word_id: int,
    result: str,
    source: str,
    review_type: str,
    article_id: int | None = None,
) -> ReviewEvent:
    """Record a review of the caller's own word, or raise ``LookupError``.

    ``word_id`` is either the legacy ``word.id`` a client already knows, or the
    caller's own ``user_word_state.id``. Both resolve through owner-scoped
    lookups, so neither can reach another user's word. Lookup failures surface as
    a 404 at the API boundary: another user's id is indistinguishable from a
    nonexistent one.
    """
    view = find_user_word(session, user, word_id)
    if view is None:
        # Same error whether the id is missing or simply not the caller's.
        raise not_found("?????")
    state = view.state
    return _apply(
        session, state, state.legacy_word_id, result, source, review_type, article_id
    )


def _apply(
    session: Session,
    state: UserWordState,
    legacy_word_id: int | None,
    result: str,
    source: str,
    review_type: str,
    article_id: int | None,
) -> ReviewEvent:
    before = state.status
    now = datetime.now(UTC)
    schedule = calculate_schedule(before, result, state.consecutive_failures, now=now)

    event = ReviewEvent(
        user_id=state.user_id,
        word_id=legacy_word_id,
        lexicon_entry_id=state.lexicon_entry_id,
        timestamp=now,
        result=result,
        source=source,
        article_id=article_id,
        status_before=before,
        status_after=schedule.status,
        review_type=review_type,
    )

    state.status = schedule.status
    state.next_review_at = schedule.next_review_at
    state.consecutive_failures = schedule.consecutive_failures
    state.last_review = now
    if result == "know":
        state.recall_success += 1
    elif result == "fail":
        state.recall_fail += 1

    session.add(event)
    session.commit()
    session.refresh(event)
    return event
