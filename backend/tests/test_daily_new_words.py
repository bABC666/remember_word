"""G8 / T16: the daily new-word target really limits the study queue.

The setting used to be inert: ``GET /api/study/today`` read only its own ``limit``
parameter, so the queue was "every due word plus every ``new`` word, up to 50", and
the settings page lied to the user (P-5).

The rule these tests pin is deliberately *not* "at most N new words per request" --
a per-request cap is satisfied by refreshing the page. It is "at most N distinct
words may leave the ``new`` state in one UTC day", which is a property of the stored
review history, so repeating the request, reviewing words, or opening a second tab
cannot raise it. The marker for that transition is ``review_event.status_before ==
'new'``, a column that has existed since the V1.1 schema and is written by the only
code that records reviews.

The other half of the contract is what must NOT change: due and ``weak`` words are
never reduced by the new-word budget, and they take the limited queue slots first.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Lexicon, ReviewEvent, UserLexicon, UserSettings, UserWordState

#: The number the settings page shows when the user never changed it.
DEFAULT_TARGET = 15


# --- helpers -----------------------------------------------------------------


def _queue(world, **params) -> dict:
    response = world.client.get("/api/study/today", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _new_words(payload: dict) -> list[dict]:
    return [item for item in payload["words"] if item["status"] == "new"]


def _due_words(payload: dict) -> list[dict]:
    """Everything the queue serves that is not a new word: due and weak words."""
    return [item for item in payload["words"] if item["status"] != "new"]


def _budget(payload: dict) -> dict:
    return payload["daily_new_words"]


def _set_target(world, value: int) -> None:
    response = world.client.put("/api/settings", json={"daily_new_words": value})
    assert response.status_code == 200, response.text


def _set_target_in_db(world, value: int) -> None:
    """Write a value the settings API refuses (its range is 1-500).

    The column can hold anything, so the queue has to stay sane for a value written
    by hand or by an older version.
    """
    with world.session() as session:
        session.get(UserSettings, world.user_id).daily_new_words = value
        session.commit()


def _set_lexicon_target(world, lexicon_id: int, value: int) -> None:
    """Set the per-lexicon pace.

    No endpoint writes this column today (the roadmap's "written by the enable call"
    is not what the code does), so a test writes it the way an operator would.
    """
    with world.session() as session:
        membership = session.scalar(
            select(UserLexicon).where(
                UserLexicon.user_id == world.user_id,
                UserLexicon.lexicon_id == lexicon_id,
            )
        )
        assert membership is not None
        membership.daily_new_words = value
        session.commit()


def _review(world, state_id: int, result: str = "know") -> None:
    response = world.client.post(
        f"/api/study/word-states/{state_id}/review",
        json={"result": result, "source": "daily", "review_type": "recall"},
    )
    assert response.status_code == 200, response.text


def _make_due(world, state_id: int, *, days_ago: int = 1) -> None:
    """Make a word look like one studied before today and due now.

    Deliberately writes **no** review event: a word reviewed today would be new
    material consumed today, and these helpers exist to build the other half of the
    queue.
    """
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    with world.session() as session:
        state = session.get(UserWordState, state_id)
        state.status = "familiar"
        state.last_review = moment
        state.next_review_at = moment
        session.commit()


def _review_at(world, state_id: int, entry_id: int, when: datetime) -> None:
    """Write one review event with an explicit timestamp, and move the state with it.

    Used for the day-boundary cases, where "today" cannot be reached by waiting.
    """
    with world.session() as session:
        session.add(
            ReviewEvent(
                user_id=world.user_id,
                lexicon_entry_id=entry_id,
                timestamp=when,
                result="know",
                source="daily",
                status_before="new",
                status_after="familiar",
                review_type="recall",
            )
        )
        state = session.get(UserWordState, state_id)
        state.status = "familiar"
        state.last_review = when
        state.next_review_at = when + timedelta(days=1)
        session.commit()


def _add_new_words(world, count: int, *, prefix: str = "word") -> list[int]:
    return [world.add_word(f"{prefix}-{index}")[0] for index in range(count)]


# --- the daily cap -----------------------------------------------------------

def test_raising_target_after_finishing_selected_lexicon_unlocks_more_words(world):
    lexicon_id = world.lexicon("NETEM")
    with world.session() as session:
        for index in range(60):
            _entry_in(session, lexicon_id, f"extra-{index}")
        session.commit()
    assert world.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    first = _new_words(_queue(world))
    assert len(first) == DEFAULT_TARGET
    for item in first:
        _review(world, item["word_state_id"])
    assert not _new_words(_queue(world))
    _set_target(world, 60)
    after = _queue(world)
    assert _budget(after) == {"target": 60, "consumed_today": 15, "remaining": 45}
    assert len(_new_words(after)) == 45
    assert {item["word_state_id"] for item in first}.isdisjoint(
        item["word_state_id"] for item in after["words"]
    )


def test_selected_lexicon_refills_beyond_the_first_queue_page(world):
    lexicon_id = world.lexicon("NETEM")
    with world.session() as session:
        for index in range(100):
            _entry_in(session, lexicon_id, f"page-{index}")
        session.commit()
    _set_target(world, 100)
    assert world.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    first = _new_words(_queue(world))
    assert len(first) == 50
    for item in first:
        _review(world, item["word_state_id"])
    second = _new_words(_queue(world))
    assert len(second) == 50
    assert {item["word_state_id"] for item in first}.isdisjoint(
        item["word_state_id"] for item in second
    )
    for item in second:
        _review(world, item["word_state_id"])
    assert _budget(_queue(world)) == {"target": 100, "consumed_today": 100, "remaining": 0}
    assert not _new_words(_queue(world))


@pytest.mark.parametrize("target", [101, 500])
def test_settings_accept_new_word_targets_up_to_500(world, target):
    _set_target(world, target)
    assert world.client.get("/api/settings").json()["daily_new_words"] == target


@pytest.mark.parametrize("target", [0, 501])
def test_settings_reject_new_word_targets_outside_1_to_500(world, target):
    response = world.client.put("/api/settings", json={"daily_new_words": target})
    assert response.status_code == 422
    assert world.client.get("/api/settings").json()["daily_new_words"] == DEFAULT_TARGET


def test_selected_lexicon_can_study_500_words_across_ten_queue_pages(world):
    lexicon_id = world.lexicon("NETEM")
    with world.session() as session:
        for index in range(510):
            _entry_in(session, lexicon_id, f"large-target-{index}")
        session.commit()
    _set_target(world, 500)
    assert world.client.post(f"/api/lexicons/{lexicon_id}/select").status_code == 200
    studied = set()
    for _ in range(10):
        words = _new_words(_queue(world))
        assert len(words) == 50
        ids = {item["word_state_id"] for item in words}
        assert studied.isdisjoint(ids)
        studied.update(ids)
        for item in words:
            _review(world, item["word_state_id"])
    assert len(studied) == 500
    assert _budget(_queue(world)) == {"target": 500, "consumed_today": 500, "remaining": 0}
    assert not _new_words(_queue(world))


def test_new_words_are_capped_by_the_daily_target(world) -> None:
    _set_target(world, 2)
    _add_new_words(world, 5)

    payload = _queue(world)

    assert len(_new_words(payload)) == 2
    assert _budget(payload) == {"target": 2, "consumed_today": 0, "remaining": 2}


def test_the_cap_is_cumulative_across_requests_and_reviews(world) -> None:
    """The core of P-5: the budget is spent by studying, not by asking."""
    _set_target(world, 2)
    _add_new_words(world, 5)

    served = _new_words(_queue(world))
    assert len(served) == 2
    for item in served:
        _review(world, item["word_state_id"])

    after = _queue(world)
    assert _new_words(after) == []
    assert _budget(after) == {"target": 2, "consumed_today": 2, "remaining": 0}


def test_repeating_the_request_does_not_hand_out_more_new_words(world) -> None:
    _set_target(world, 3)
    _add_new_words(world, 6)

    served = [_new_words(_queue(world)) for _ in range(4)]

    assert [len(batch) for batch in served] == [3, 3, 3, 3]
    # The same three words every time, in the same order -- not three more each time.
    assert len({tuple(item["word_state_id"] for item in batch) for batch in served}) == 1


def test_the_budget_does_not_refill_after_one_review(world) -> None:
    _set_target(world, 1)
    _add_new_words(world, 4)

    served = _new_words(_queue(world))
    assert len(served) == 1
    _review(world, served[0]["word_state_id"])

    assert _new_words(_queue(world)) == []
    assert _budget(_queue(world))["consumed_today"] == 1


def test_a_zero_target_stops_new_words_without_touching_due_ones(world) -> None:
    _set_target_in_db(world, 0)
    for index in range(3):
        _make_due(world, world.add_word(f"due-{index}")[0])
    _add_new_words(world, 3, prefix="pending")

    payload = _queue(world)

    assert _new_words(payload) == []
    assert len(_due_words(payload)) == 3
    assert _budget(payload)["remaining"] == 0


def test_the_default_target_is_the_settings_default(world) -> None:
    _add_new_words(world, 1)
    assert _budget(_queue(world))["target"] == DEFAULT_TARGET


# --- what counts as a new word studied today ---------------------------------


def test_a_word_already_past_new_does_not_consume_the_budget(world) -> None:
    """Reviewing an old word is a review, not new material."""
    _set_target(world, 2)
    state_id, _entry_id = world.add_word("already-familiar", status="familiar")
    _review(world, state_id)

    payload = _queue(world)

    assert _budget(payload)["consumed_today"] == 0
    assert _budget(payload)["remaining"] == 2


def test_one_word_reviewed_twice_today_uses_one_slot(world) -> None:
    _set_target(world, 2)
    state_id, _entry_id = world.add_word("reviewed-twice")
    _review(world, state_id)
    _review(world, state_id, result="fuzzy")

    assert _budget(_queue(world))["consumed_today"] == 1


def test_a_failed_new_word_still_consumes_the_slot(world) -> None:
    """ "I did not know it" is still the first time that word was studied."""
    _set_target(world, 2)
    state_id, _entry_id = world.add_word("failed-new")
    _review(world, state_id, result="fail")

    payload = _queue(world)

    assert _budget(payload)["consumed_today"] == 1
    # A failed word becomes 'weak' and is always served, which is unchanged.
    assert [item["status"] for item in _due_words(payload)] == ["weak"]


def test_no_history_at_all_means_the_whole_target_is_available(world) -> None:
    _set_target(world, 4)
    _add_new_words(world, 2)

    payload = _queue(world)

    assert _budget(payload) == {"target": 4, "consumed_today": 0, "remaining": 4}
    assert len(_new_words(payload)) == 2


# --- the day boundary --------------------------------------------------------


def test_yesterdays_new_words_do_not_spend_todays_budget(world) -> None:
    _set_target(world, 2)
    state_id, entry_id = world.add_word("yesterday", status="familiar")
    _review_at(world, state_id, entry_id, datetime.now(UTC) - timedelta(days=1))

    payload = _queue(world)

    assert _budget(payload)["consumed_today"] == 0
    assert _budget(payload)["remaining"] == 2


def test_the_day_window_is_the_shared_utc_day(world) -> None:
    """Both edges pinned: the window is ``[start, start + 1 day)`` in UTC."""
    from app.services.day import day_bounds

    _set_target(world, 5)
    start, end = day_bounds(datetime.now(UTC).date())
    on_start, entry_start = world.add_word("on-start", status="familiar")
    before_start, entry_before = world.add_word("before-start", status="familiar")
    at_end, entry_end = world.add_word("at-end", status="familiar")

    _review_at(world, on_start, entry_start, start)
    _review_at(world, before_start, entry_before, start - timedelta(microseconds=1))
    # Written directly: the window is half-open, so the next midnight already belongs
    # to tomorrow even though the clock has not reached it.
    _review_at(world, at_end, entry_end, end)

    assert _budget(_queue(world))["consumed_today"] == 1


def test_the_day_window_helper_is_a_utc_day() -> None:
    from app.services.day import day_bounds

    start, end = day_bounds(datetime(2026, 9, 23, tzinfo=UTC).date())

    assert start == datetime(2026, 9, 23, 0, 0, tzinfo=UTC)
    assert end - start == timedelta(days=1)


# --- due words keep their place ----------------------------------------------


def test_due_words_are_never_reduced_by_the_new_word_budget(world) -> None:
    _set_target(world, 1)
    for index in range(4):
        _make_due(world, world.add_word(f"due-{index}")[0])
    _add_new_words(world, 5, prefix="pending")

    before = _queue(world)
    assert len(_due_words(before)) == 4
    assert len(_new_words(before)) == 1

    _review(world, _new_words(before)[0]["word_state_id"])

    after = _queue(world)
    assert len(_due_words(after)) == 4
    assert _new_words(after) == []


def test_due_words_take_the_limited_slots_before_new_words(world) -> None:
    """A small ``limit`` used to be filled by new words, hiding due ones."""
    _set_target(world, DEFAULT_TARGET)
    for index in range(3):
        _make_due(world, world.add_word(f"due-{index}")[0])
    _add_new_words(world, 5, prefix="fresh")

    capped = _queue(world, limit=2)
    assert len(capped["words"]) == 2
    assert all(item["status"] != "new" for item in capped["words"])

    roomy = _queue(world, limit=50)
    assert len(_due_words(roomy)) == 3
    assert len(_new_words(roomy)) == 5


def test_the_queue_stays_ordered_weak_then_due_then_new(world) -> None:
    _set_target(world, 2)
    weak_state, _entry_id = world.add_word("weak-one")
    _review(world, weak_state, result="fail")
    _make_due(world, world.add_word("due-one")[0])
    _add_new_words(world, 1, prefix="fresh")

    statuses = [item["status"] for item in _queue(world)["words"]]

    assert statuses == ["weak", "familiar", "new"]


# --- the user level and the lexicon level ------------------------------------


def test_each_lexicon_contributes_its_own_pace(world) -> None:
    first = world.lexicon("pace-a")
    second = world.lexicon("pace-b")
    _set_target(world, 12)
    _set_lexicon_target(world, first, 1)
    _set_lexicon_target(world, second, 1)
    for index in range(3):
        world.add_word(f"a-{index}", lexicon_id=first)
        world.add_word(f"b-{index}", lexicon_id=second)

    payload = _queue(world)
    served = _new_words(payload)

    assert len(served) == 2
    assert Counter(item["lexicon_id"] for item in served) == Counter({first: 1, second: 1})
    assert _budget(payload)["remaining"] == 2


def test_the_user_level_target_caps_the_sum_of_the_lexicon_paces(world) -> None:
    first = world.lexicon("cap-a")
    second = world.lexicon("cap-b")
    _set_target(world, 1)
    _set_lexicon_target(world, first, 5)
    _set_lexicon_target(world, second, 5)
    for index in range(3):
        world.add_word(f"a-{index}", lexicon_id=first)
        world.add_word(f"b-{index}", lexicon_id=second)

    payload = _queue(world)

    assert len(_new_words(payload)) == 1
    assert _budget(payload) == {"target": 1, "consumed_today": 0, "remaining": 1}


def test_a_lexicon_without_a_membership_row_is_still_capped_by_the_user_level(world) -> None:
    """A missing membership row must not hide a state that already exists."""
    with world.session() as session:
        lexicon = Lexicon(name="unjoined", visibility="private", owner_user_id=world.user_id)
        session.add(lexicon)
        session.flush()
        lexicon_id = lexicon.id
        state = UserWordState(
            user_id=world.user_id,
            lexicon_entry_id=_entry_in(session, lexicon_id, "orphan-word"),
        )
        session.add(state)
        session.commit()
    _set_target(world, 1)
    world.add_word("joined-word")

    payload = _queue(world)

    assert len(_new_words(payload)) == 1
    assert _budget(payload)["remaining"] == 1


def _entry_in(session, lexicon_id: int, word: str) -> int:
    from app.models import LexiconEntry

    entry = LexiconEntry(
        lexicon_id=lexicon_id,
        word=word,
        normalized_word=word.casefold(),
        source_meanings=["释义"],
        source_raw=word,
        default_anchor=word,
    )
    session.add(entry)
    session.flush()
    return entry.id


def test_a_disabled_lexicon_keeps_its_words_in_the_queue(world) -> None:
    """Known, deliberate boundary: ``enabled`` does not change eligibility here.

    What "disabled" means for the queue (leave it entirely, or only stop offering new
    words?) is a product decision, and filtering here would silently hide learning
    state that already exists. No UI sets this column either. Documented as a
    residual in the design record rather than changed in this batch.
    """
    lexicon_id = world.lexicon("disabled-lex")
    world.add_word("still-here", lexicon_id=lexicon_id)
    with world.session() as session:
        membership = session.scalar(
            select(UserLexicon).where(
                UserLexicon.user_id == world.user_id,
                UserLexicon.lexicon_id == lexicon_id,
            )
        )
        membership.enabled = False
        session.commit()

    payload = _queue(world)

    assert [item["word"] for item in payload["words"]] == ["still-here"]


# --- isolation and the untouched parts of the contract -----------------------


def test_one_users_reviews_do_not_spend_another_users_budget(two_worlds) -> None:
    first, second = two_worlds
    for world in (first, second):
        _set_target(world, 1)
    for index in range(3):
        first.add_word(f"first-{index}")
        second.add_word(f"second-{index}")

    served = _new_words(_queue(first))
    assert len(served) == 1
    _review(first, served[0]["word_state_id"])

    assert _new_words(_queue(first)) == []
    assert _budget(_queue(first))["consumed_today"] == 1

    # The other account is untouched: full budget, and none of the first user's words.
    other = _queue(second)
    assert len(_new_words(other)) == 1
    assert _budget(other) == {"target": 1, "consumed_today": 0, "remaining": 1}
    assert all(item["word"].startswith("second-") for item in other["words"])


def test_the_queue_response_keeps_its_existing_shape(world) -> None:
    state_id, _entry_id = world.add_word("shape-check")
    payload = _queue(world)

    assert payload["total"] == len(payload["words"])
    assert payload["words"][0]["word"] == "shape-check"
    assert payload["words"][0]["word_state_id"] == state_id
    assert set(payload["daily_new_words"]) == {"target", "consumed_today", "remaining"}


@pytest.mark.parametrize("limit", [0, 201])
def test_the_limit_parameter_bounds_are_unchanged(world, limit: int) -> None:
    assert world.client.get("/api/study/today", params={"limit": limit}).status_code == 422


def test_reading_a_word_from_an_article_is_still_a_review_that_spends_the_budget(world) -> None:
    """Any review that moves a word out of ``new`` counts, whatever the screen.

    Only the study endpoints write reviews today, but the rule is stated in terms of
    the recorded transition, not of the calling screen, so a future reading screen
    cannot spend the day's budget twice.
    """
    _set_target(world, 3)
    state_id, _entry_id = world.add_word("from-reading")
    response = world.client.post(
        f"/api/study/word-states/{state_id}/review",
        json={"result": "know", "source": "reading", "review_type": "recognition"},
    )
    assert response.status_code == 200, response.text

    assert _budget(_queue(world))["consumed_today"] == 1
