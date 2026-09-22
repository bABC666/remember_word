from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import ReviewEvent, User, UserWordState
from app.services.scheduler import calculate_schedule
from app.services.userdata import (
    WordView,
    load_user_article,
    load_user_word,
    load_user_word_state,
)

#: The scheduling rules themselves are unchanged from V1.1. What changed in V1.2
#: is the input: the word must belong to the acting user, which is enforced by
#: loading it through an owner-scoped query.
#:
#: There are two entry points, one per identifier namespace, and neither falls back
#: to the other:
#:
#: * :func:`record_review_for_user` -- the legacy ``word.id``;
#: * :func:`record_review_for_user_state` -- this user's own
#:   ``user_word_state.id``.
#:
#: Both finish in :func:`_record`, which validates the referenced article's
#: ownership **before** anything is written.


def record_review_for_user(
    session: Session,
    user: User,
    word_id: int,
    result: str,
    source: str,
    review_type: str,
    article_id: int | None = None,
) -> ReviewEvent:
    """Record a review of the caller's own word, addressed by its legacy ``word.id``.

    Raises ``NotFoundError`` for an id that does not exist *or* is not the
    caller's, which the API turns into the same 404.
    """
    return _record(
        session,
        user,
        load_user_word(session, user, word_id),
        result,
        source,
        review_type,
        article_id,
    )


def record_review_for_user_state(
    session: Session,
    user: User,
    state_id: int,
    result: str,
    source: str,
    review_type: str,
    article_id: int | None = None,
) -> ReviewEvent:
    """Record a review addressed by the caller's own ``user_word_state.id``.

    Needed for words that have no legacy ``word`` row at all -- every word a user
    adds from an article. The id is resolved through an owner-scoped query, so a
    state id belonging to someone else is a 404 like any other missing row.
    """
    return _record(
        session,
        user,
        load_user_word_state(session, user, state_id),
        result,
        source,
        review_type,
        article_id,
    )


def _record(
    session: Session,
    user: User,
    view: WordView,
    result: str,
    source: str,
    review_type: str,
    article_id: int | None,
) -> ReviewEvent:
    if article_id is not None:
        # Ownership of the referenced article is checked BEFORE the first write.
        # Without this a review of one's own word could be attached to somebody
        # else's article id, and the rejected request would already have moved the
        # word's status, counters and schedule.
        load_user_article(session, user, article_id)
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
