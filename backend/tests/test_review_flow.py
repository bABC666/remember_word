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


def _seed_word_with_state(session, *, word: str, status: str = "new", legacy: bool = False):
    """Create a lexicon entry plus one user's learning state for it.

    ``legacy=True`` also creates the V1.1 ``word`` row, which is what the legacy
    routes address. With ``legacy=False`` the word can only be reached through its
    ``user_word_state.id``.
    """
    from app.models import Lexicon, LexiconEntry, User, UserWordState, Word

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
    legacy_word_id = None
    if legacy:
        now = datetime.now(UTC)
        row = Word(
            word=word,
            phonetic="",
            part_of_speech="",
            source_meanings=[],
            source_raw=word,
            anchor=word,
            semantic_note="",
            status=status,
            first_seen=now,
            recall_success=0,
            recall_fail=0,
            consecutive_failures=0,
            context_exposure=0,
            possible_issue=False,
            notes="",
            created_at=now,
            updated_at=now,
            user_id=user.id,
            lexicon_entry_id=entry.id,
        )
        session.add(row)
        session.flush()
        legacy_word_id = row.id
    state = UserWordState(
        user_id=user.id,
        lexicon_entry_id=entry.id,
        status=status,
        legacy_word_id=legacy_word_id,
    )
    session.add(state)
    session.commit()
    return user, entry, state


def test_review_by_state_id_writes_full_event_and_updates_the_users_state(session) -> None:
    """A review updates the acting user's state and records a full event."""
    from app.models import ReviewEvent
    from app.services.study import record_review_for_user_state

    user, _entry, state = _seed_word_with_state(session, word="retain")
    event = record_review_for_user_state(session, user, state.id, "fail", "daily", "recall")

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


def test_review_by_legacy_word_id_writes_the_legacy_bridge(session) -> None:
    """The legacy namespace records ``word_id`` as well as ``lexicon_entry_id``."""
    from app.services.study import record_review_for_user

    user, entry, state = _seed_word_with_state(session, word="legacy-retain", legacy=True)
    event = record_review_for_user(session, user, state.legacy_word_id, "know", "daily", "recall")

    assert event.word_id == state.legacy_word_id
    assert event.lexicon_entry_id == entry.id
    assert event.status_after == "familiar"


def test_the_two_entry_points_consult_exactly_one_lookup_each(
    session, monkeypatch
) -> None:
    """Each service function resolves one namespace, and never falls back.

    Asserted on the wiring rather than on ids: both tables start numbering at 1, so
    a state id and a legacy word id can coincide, and a test that leans on them
    differing proves nothing. This watches which lookup each entry point calls.
    """
    from app.services import study
    from app.services.study import record_review_for_user, record_review_for_user_state

    seen: list[str] = []
    real_legacy = study.load_user_word
    real_state = study.load_user_word_state

    def legacy(session_, user_, word_id_):
        seen.append("legacy")
        return real_legacy(session_, user_, word_id_)

    def state_lookup(session_, user_, state_id_):
        seen.append("state")
        return real_state(session_, user_, state_id_)

    monkeypatch.setattr(study, "load_user_word", legacy)
    monkeypatch.setattr(study, "load_user_word_state", state_lookup)

    user, _entry, row = _seed_word_with_state(session, word="wired", legacy=True)
    record_review_for_user(session, user, row.legacy_word_id, "know", "daily", "recall")
    assert seen == ["legacy"], "the legacy entry point must not try the state namespace"

    record_review_for_user_state(session, user, row.id, "know", "daily", "recall")
    assert seen == ["legacy", "state"], "the state entry point must not try the legacy one"


def test_the_ambiguous_lookup_helper_is_gone() -> None:
    """``find_user_word`` existed only to implement the two-namespace fallback."""
    from app.services import userdata

    assert not hasattr(userdata, "find_user_word")


def test_review_is_scoped_to_the_acting_user(session) -> None:
    """Another user's word id must not be reviewable, and must not be revealed."""
    from app.services.study import record_review_for_user_state

    owner, _entry, state = _seed_word_with_state(session, word="retreat")
    intruder, _e2, _s2 = _seed_word_with_state(session, word="withdraw")

    # The owner can review their own word.
    record_review_for_user_state(session, owner, state.id, "know", "daily", "recall")

    # The intruder cannot, and gets the same failure as for a missing id.
    with pytest.raises(LookupError):
        record_review_for_user_state(session, intruder, state.id, "know", "daily", "recall")
    with pytest.raises(LookupError):
        record_review_for_user_state(session, intruder, 999999, "know", "daily", "recall")


def test_review_of_a_nonexistent_word_raises_lookup_error(session) -> None:
    from app.services.study import record_review_for_user_state

    user, _entry, _state = _seed_word_with_state(session, word="setback")
    with pytest.raises(LookupError):
        record_review_for_user_state(session, user, 123456, "know", "daily", "recall")
