from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Lexicon,
    LexiconEntry,
    ReviewEvent,
    User,
    UserLexicon,
    UserSettings,
    UserWordState,
)
from app.services.day import today_bounds
from app.services.scheduler import calculate_schedule
from app.services.userdata import (
    WordView,
    get_or_create_word_state,
    load_readable_lexicon,
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
#:
#: The other half of this module is the read side: :func:`build_today_queue` answers
#: "what should this user study now?", including how much of the daily new-word
#: allowance is left. See ``docs/V1.2-PHASE2.8-E-DAILY-NEW-WORDS-DESIGN.md``.


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


# --- the study queue and the daily new-word allowance ------------------------
#
# G8 / T16: ``user_settings.daily_new_words`` used to be inert. The queue read only
# its own ``limit`` parameter, so the settings page described a limit that did not
# exist (P-5).
#
# The rule is deliberately not "at most N new words per response": a per-request cap
# is satisfied by refreshing the page. It is "at most N distinct words may leave the
# ``new`` state in one UTC day", which is a property of the stored review history and
# so cannot be raised by asking again.
#
# The marker is ``review_event.status_before == 'new'``. It has existed since the V1.1
# schema; :func:`_apply` above writes it before changing anything; and the scheduler
# always moves a reviewed word out of ``new``. The first review of a word -- whatever
# the result, ``fail`` included -- is therefore exactly the moment that word was first
# studied, and counting those events counts new material actually taken on.
#
# ``user_lexicon.daily_new_words`` paces one lexicon; ``user_settings.daily_new_words``
# caps the day. Both apply: a lexicon may contribute at most its own number, and the
# day's total may not exceed the user-level number. Nothing writes the per-lexicon
# column today (the enable endpoint only takes ``enabled``), so it currently holds its
# model default -- but the queue honours it as soon as anything does.

#: The default ``models.UserSettings.daily_new_words`` is declared with. Read from the
#: model rather than repeated here, so the two cannot drift apart.
DEFAULT_DAILY_NEW_WORDS: int = UserSettings.__table__.c.daily_new_words.default.arg


@dataclass(frozen=True)
class NewWordBudget:
    """How many new words this user may still start today.

    ``remaining`` is an allowance, not a promise of candidates: the queue may return
    fewer words simply because the user has fewer unstudied ones.
    """

    #: The user-level setting, as shown on the settings page.
    target: int
    #: Distinct new words that left ``new`` today, across every lexicon.
    consumed_today: int
    #: The smaller of the user-level allowance and the sum of the per-lexicon paces.
    remaining: int
    #: What the user-level setting alone still allows.
    user_room: int
    #: ``lexicon_id`` -> how many more new words that lexicon may contribute today.
    lexicon_room: dict[int, int]

    def room_for(self, lexicon_id: int) -> int:
        return self.lexicon_room.get(lexicon_id, self.user_room)


@dataclass(frozen=True)
class TodayQueue:
    """One response's worth of study material, plus the allowance behind it."""

    words: list[WordView]
    budget: NewWordBudget


def build_today_queue(
    session: Session,
    user: User,
    *,
    limit: int,
    now: datetime | None = None,
    lexicon_id: int | None = None,
) -> TodayQueue:
    """What this user should study now: due words first, then new ones in budget.

    ``limit`` still caps the whole response. Due and ``weak`` words take those slots
    first -- the old ordering sorted ``next_review_at`` ascending, and ``new`` words
    have no ``next_review_at``, so a small ``limit`` was filled with new words and the
    due ones were hidden. The daily allowance additionally caps only the new words, so
    it can never reduce the reviews.
    """
    moment = now or datetime.now(UTC)
    budget = new_word_budget(session, user, now=moment)
    if lexicon_id is not None:
        load_readable_lexicon(session, user, lexicon_id)
        _ensure_selected_states(session, user, lexicon_id, min(limit, budget.user_room))
    due = _due_rows(session, user, moment, limit, lexicon_id)

    fresh: list[tuple[UserWordState, LexiconEntry]] = []
    room = limit - len(due)
    if room > 0 and budget.remaining > 0:
        fresh = _new_rows(session, user, room, budget, lexicon_id)

    views = [WordView(state=state, entry=entry) for state, entry in (*due, *fresh)]
    return TodayQueue(words=views, budget=budget)


def _ensure_selected_states(session: Session, user: User, lexicon_id: int, limit: int) -> None:
    """Make only the next page of system entries studyable; never enrol in bulk."""
    pending = session.scalar(
        select(func.count()).select_from(UserWordState)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(UserWordState.user_id == user.id, UserWordState.status == "new",
               LexiconEntry.lexicon_id == lexicon_id)
    ) or 0
    limit = max(0, limit - pending)
    if limit <= 0:
        return
    existing = select(UserWordState.lexicon_entry_id).where(UserWordState.user_id == user.id)
    entries = session.scalars(
        select(LexiconEntry).where(LexiconEntry.lexicon_id == lexicon_id,
                                   LexiconEntry.id.not_in(existing))
        .order_by(LexiconEntry.sequence, LexiconEntry.id).limit(limit)
    ).all()
    for entry in entries:
        get_or_create_word_state(session, user, entry)
    session.commit()


def _readable_lexicon_ids(user: User):
    # Withdrawal keeps historical states intact. A fallback queue must still
    # respect library visibility rather than re-serving a withdrawn library.
    return select(Lexicon.id).where(or_(
        Lexicon.owner_user_id == user.id,
        (Lexicon.owner_user_id.is_(None)) & (Lexicon.visibility == "public"),
    ))


def _due_rows(
    session: Session, user: User, moment: datetime, limit: int, lexicon_id: int | None = None
) -> list[tuple[UserWordState, LexiconEntry]]:
    """Words that are not new and are due, ``weak`` first.

    Exactly the words the queue served before this change, minus the ``new`` ones:
    ``status != 'new'`` plus one of "unscheduled" (rows that predate scheduling),
    "due now", or "weak" (always offered, whatever the schedule says).
    """
    lexicon_filter = [] if lexicon_id is None else [LexiconEntry.lexicon_id == lexicon_id]
    return list(
        session.execute(
            select(UserWordState, LexiconEntry)
            .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
            .where(
                UserWordState.user_id == user.id,
                LexiconEntry.lexicon_id.in_(_readable_lexicon_ids(user)),
                *lexicon_filter,
                UserWordState.status != "new",
                or_(
                    UserWordState.next_review_at.is_(None),
                    UserWordState.next_review_at <= moment,
                    UserWordState.status == "weak",
                ),
            )
            .order_by(
                UserWordState.status != "weak",
                UserWordState.next_review_at,
                UserWordState.first_seen,
                UserWordState.id,
            )
            .limit(limit)
        ).all()
    )


def _new_rows(
    session: Session, user: User, room: int, budget: NewWordBudget,
    lexicon_id: int | None = None,
) -> list[tuple[UserWordState, LexiconEntry]]:
    """Up to ``room`` new words, spending the allowance as it goes.

    The candidates are read oldest-first and taken while both budgets allow: the day's
    total may not exceed the user-level allowance, and one lexicon may not exceed its
    own pace. A candidate whose lexicon is already spent is skipped, so a slow-paced
    lexicon cannot block the others behind it in the ordering.
    """
    lexicon_filter = [] if lexicon_id is None else [LexiconEntry.lexicon_id == lexicon_id]
    candidates = session.execute(
        select(UserWordState, LexiconEntry)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(
            UserWordState.user_id == user.id,
            LexiconEntry.lexicon_id.in_(_readable_lexicon_ids(user)),
            *lexicon_filter,
            UserWordState.status == "new",
        )
        .order_by(LexiconEntry.sequence, UserWordState.first_seen, UserWordState.id)
        .limit(room)
    ).all()

    taken: list[tuple[UserWordState, LexiconEntry]] = []
    spent: dict[int, int] = {}
    for state, entry in candidates:
        if len(taken) >= budget.user_room:
            break
        lexicon_id = entry.lexicon_id
        if spent.get(lexicon_id, 0) >= budget.room_for(lexicon_id):
            continue
        spent[lexicon_id] = spent.get(lexicon_id, 0) + 1
        taken.append((state, entry))
    return taken


def new_word_budget(
    session: Session, user: User, *, now: datetime | None = None
) -> NewWordBudget:
    """This user's new-word allowance for the current UTC day."""
    start, end = today_bounds(now)
    target = _daily_new_words(session, user)
    consumed = _new_words_started(session, user, start, end)
    consumed_today = sum(consumed.values())
    user_room = max(0, target - consumed_today)
    lexicon_room = _lexicon_room(session, user, target, consumed)
    # No lexicon is known at all (no membership, nothing studied, nothing pending):
    # the user-level setting is then the only pace that applies.
    total_room = sum(lexicon_room.values()) if lexicon_room else user_room
    return NewWordBudget(
        target=target,
        consumed_today=consumed_today,
        remaining=min(user_room, total_room),
        user_room=user_room,
        lexicon_room=lexicon_room,
    )


def _daily_new_words(session: Session, user: User) -> int:
    settings = session.get(UserSettings, user.id)
    if settings is None:
        return int(DEFAULT_DAILY_NEW_WORDS)
    # A value written by hand can be zero or negative; neither may turn into a
    # negative allowance.
    return max(0, int(settings.daily_new_words))


def _new_words_started(
    session: Session, user: User, start: datetime, end: datetime
) -> dict[int, int]:
    """Distinct new words that left ``new`` inside the window, per lexicon.

    One review row is one event, so the same word reviewed twice in a day appears
    once (``DISTINCT``), and the per-lexicon counts are disjoint -- their sum is the
    day's total. Events whose entry was deleted carry a NULL ``lexicon_entry_id`` and
    therefore cannot be joined to a lexicon; the 409 guard on lexicon deletion means
    that cannot happen while learning state exists.
    """
    rows = session.execute(
        select(
            LexiconEntry.lexicon_id,
            func.count(func.distinct(ReviewEvent.lexicon_entry_id)),
        )
        .join(LexiconEntry, LexiconEntry.id == ReviewEvent.lexicon_entry_id)
        .where(
            ReviewEvent.user_id == user.id,
            ReviewEvent.status_before == "new",
            ReviewEvent.timestamp >= start,
            ReviewEvent.timestamp < end,
        )
        .group_by(LexiconEntry.lexicon_id)
    ).all()
    return {lexicon_id: count for lexicon_id, count in rows}


def _lexicon_room(
    session: Session, user: User, target: int, consumed: dict[int, int]
) -> dict[int, int]:
    """How many more new words each known lexicon may contribute today.

    A lexicon is "known" when the user is enrolled in it, has already started words
    from it today, or has unstudied words in it -- so a state whose lexicon has no
    ``user_lexicon`` row is still offered (paced by the user-level setting) instead of
    silently disappearing from the queue.
    """
    paces = {
        # The legacy membership default is not a separately configured pace.
        # It follows the personal goal, including increases made during the day.
        row.lexicon_id: (
            target if row.daily_new_words == DEFAULT_DAILY_NEW_WORDS else row.daily_new_words
        )
        for row in session.scalars(
            select(UserLexicon).where(UserLexicon.user_id == user.id)
        )
    }
    pending = set(
        session.scalars(
            select(LexiconEntry.lexicon_id)
            .join(UserWordState, UserWordState.lexicon_entry_id == LexiconEntry.id)
            .where(UserWordState.user_id == user.id, UserWordState.status == "new")
            .distinct()
        )
    )
    return {
        lexicon_id: max(0, paces.get(lexicon_id, target) - consumed.get(lexicon_id, 0))
        for lexicon_id in set(paces) | set(consumed) | pending
    }
