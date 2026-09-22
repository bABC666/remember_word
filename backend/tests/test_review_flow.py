from datetime import UTC, datetime

import pytest


@pytest.mark.parametrize(
    ("initial", "result", "expected"),
    [
        ("new", "fail", "weak"),
        ("new", "fuzzy", "learning"),
        ("new", "know", "familiar"),
        ("weak", "know", "learning"),
        ("known", "know", "mastered"),
    ],
)
def test_transparent_status_transitions(initial: str, result: str, expected: str) -> None:
    """The V1.1 scheduling rules are unchanged in V1.2."""
    from app.services.scheduler import calculate_schedule

    schedule = calculate_schedule(initial, result, 0, now=datetime(2026, 9, 21, tzinfo=UTC))
    assert schedule.status == expected
    assert schedule.next_review_at > datetime(2026, 9, 21, tzinfo=UTC)


def _seed_word_with_state(session, *, word: str, status: str = "new"):
    """Create a lexicon entry plus one user's learning state for it."""
    from app.models import Lexicon, LexiconEntry, User, UserWordState

    user = User(username=f"u-{word}", display_name=word)
    session.add(user)
    session.flush()
    lexicon = Lexicon(name=f"lex-{word}", visibility="private", owner_user_id=user.id)
    session.add(lexicon)
    session.flush()
    entry = LexiconEntry(
        lexicon_id=lexicon.id,
        word=word,
        normalized_word=word.casefold(),
        source_meanings=["保留"],
        source_raw=word,
    )
    session.add(entry)
    session.flush()
    state = UserWordState(
        user_id=user.id, lexicon_entry_id=entry.id, status=status, legacy_word_id=None
    )
    session.add(state)
    session.commit()
    return user, entry, state


def test_review_writes_full_event_and_updates_the_users_state(session) -> None:
    """A review updates the acting user's state and records a full event."""
    from app.models import ReviewEvent
    from app.services.study import record_review_for_user

    user, _entry, state = _seed_word_with_state(session, word="retain")
    event = record_review_for_user(session, user, state.id, "fail", "daily", "recall")

    assert event.status_before == "new"
    assert event.status_after == "weak"
    assert event.result == "fail"
    assert event.review_type == "recall"
    assert event.user_id == user.id

    session.refresh(state)
    assert state.recall_fail == 1
    assert state.status == "weak"
    assert state.next_review_at is not None
    assert session.query(ReviewEvent).count() == 1


def test_review_is_scoped_to_the_acting_user(session) -> None:
    """Another user's word id must not be reviewable, and must not be revealed."""
    from app.services.study import record_review_for_user

    owner, _entry, state = _seed_word_with_state(session, word="retreat")
    intruder, _e2, _s2 = _seed_word_with_state(session, word="withdraw")

    # The owner can review their own word.
    record_review_for_user(session, owner, state.id, "know", "daily", "recall")

    # The intruder cannot, and gets the same failure as for a missing id.
    with pytest.raises(LookupError):
        record_review_for_user(session, intruder, state.id, "know", "daily", "recall")
    with pytest.raises(LookupError):
        record_review_for_user(session, intruder, 999999, "know", "daily", "recall")


def test_review_of_a_nonexistent_word_raises_lookup_error(session) -> None:
    from app.services.study import record_review_for_user

    user, _entry, _state = _seed_word_with_state(session, word="setback")
    with pytest.raises(LookupError):
        record_review_for_user(session, user, 123456, "know", "daily", "recall")
