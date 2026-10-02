"""Deleting a lexicon must never delete the learning progress inside it.

``user_word_state.lexicon_entry_id`` is declared ``ON DELETE CASCADE``, so removing
a lexicon does not stop at the lexicon: its entries go, and every user's learning
state for those entries goes with them -- review status, consecutive failures, the
next review time and the success/failure counters -- while the exposure and review
links that identify those words are cleared to NULL. The API therefore refuses the
delete with 409 instead of performing a silent, irreversible loss of study history.

The guard is deliberately narrow: it refuses a delete that would destroy learning
state, not every delete of a non-empty lexicon. A curated list nobody has studied
yet still deletes, and that boundary is asserted here so a later "improvement"
cannot quietly turn the guard into a blanket refusal.

Refusal order is part of the contract: ownership is decided before the learning-state
lookup, so another user's lexicon -- and whether it holds learning records -- is never
confirmed to exist. That case must stay 404, never 409.
"""

from __future__ import annotations

from sqlalchemy import select


def state_ids_in(world, lexicon_id: int) -> set[int]:
    """Every ``user_word_state`` row attached to a lexicon's entries."""
    from app.models import LexiconEntry, UserWordState

    with world.session() as session:
        return set(
            session.scalars(
                select(UserWordState.id)
                .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
                .where(LexiconEntry.lexicon_id == lexicon_id)
            ).all()
        )


def state_snapshot(world, state_id: int) -> tuple[object, ...]:
    """The learning fields a cascade delete would destroy."""
    from app.models import UserWordState

    with world.session() as session:
        state = session.get(UserWordState, state_id)
        assert state is not None, f"learning state {state_id} disappeared"
        return (
            state.status,
            state.recall_success,
            state.recall_fail,
            state.consecutive_failures,
            state.next_review_at,
            state.last_review,
        )


def entry_count(world, lexicon_id: int) -> int:
    from app.models import LexiconEntry

    with world.session() as session:
        return len(
            session.scalars(
                select(LexiconEntry.id).where(LexiconEntry.lexicon_id == lexicon_id)
            ).all()
        )


# --- (a) the delete is refused --------------------------------------------


def test_deleting_a_lexicon_with_learning_records_is_refused(world) -> None:
    lexicon_id = world.lexicon("studied-lexicon")
    first_state, _ = world.add_word("studied-one", lexicon_id=lexicon_id, status="weak")
    second_state, _ = world.add_word("studied-two", lexicon_id=lexicon_id)
    world.add_review(first_state, "fail")

    response = world.client.delete(f"/api/lexicons/{lexicon_id}")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    # The message must be actionable: what is at stake, how much of it, and that
    # the request changed nothing.
    # Both states hold progress, and the review is a third learning record.
    assert "3 条学习记录" in detail, detail
    assert "学习记录" in detail, detail
    assert "已拒绝删除" in detail, detail

    # A refusal is not a partial delete: the lexicon is still there and still works.
    assert world.client.get(f"/api/lexicons/{lexicon_id}").status_code == 200
    assert state_ids_in(world, lexicon_id) == {first_state, second_state}


# --- (b) nothing moved ----------------------------------------------------


def test_the_refused_delete_changes_no_learning_state(world) -> None:
    lexicon_id = world.lexicon("studied-lexicon")
    state_id, _ = world.add_word("kept", lexicon_id=lexicon_id, status="weak")
    world.add_review(state_id, "fail")
    world.add_review(state_id, "know")

    states_before = state_ids_in(world, lexicon_id)
    entries_before = entry_count(world, lexicon_id)
    snapshot_before = state_snapshot(world, state_id)
    words_before = world.client.get("/api/words").json()

    assert world.client.delete(f"/api/lexicons/{lexicon_id}").status_code == 409

    assert state_ids_in(world, lexicon_id) == states_before
    assert entry_count(world, lexicon_id) == entries_before
    assert state_snapshot(world, state_id) == snapshot_before
    # The user's study view is intact too, not merely the row count.
    assert world.client.get("/api/words").json() == words_before
    assert world.client.get("/api/study/today").status_code == 200


def test_the_refused_delete_keeps_the_learning_history_addressable(world) -> None:
    """Review history and the word detail must survive the refusal."""
    lexicon_id = world.lexicon("studied-lexicon")
    state_id, _ = world.add_word("kept", lexicon_id=lexicon_id, status="weak")
    review_id = world.add_review(state_id, "know")
    snapshot_before = state_snapshot(world, state_id)

    assert world.client.delete(f"/api/lexicons/{lexicon_id}").status_code == 409

    detail = world.client.get(f"/api/words/state/{state_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["word"] == "kept"
    # Same status and schedule the review produced, not a reset word.
    assert payload["status"] == snapshot_before[0]
    assert payload["next_review_at"] is not None
    assert [event["id"] for event in payload["review_history"]] == [review_id]


# --- (c) an empty lexicon still deletes -----------------------------------


def test_an_empty_lexicon_can_still_be_deleted(world) -> None:
    created = world.client.post("/api/lexicons", json={"name": "空词库"})
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    assert state_ids_in(world, new_id) == set()

    response = world.client.delete(f"/api/lexicons/{new_id}")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert world.client.get(f"/api/lexicons/{new_id}").status_code == 404


def test_a_lexicon_with_untouched_entries_can_still_be_deleted(world) -> None:
    """The guard is about learning state, not about emptiness.

    A curated list whose words nobody has studied yet holds no progress to lose, so
    it stays deletable. Asserting this keeps the guard narrow: blocking every
    non-empty lexicon would be a different, unrequested behaviour change.
    """
    from app.models import LexiconEntry

    lexicon_id = world.lexicon("untouched-lexicon")
    with world.session() as session:
        session.add(
            LexiconEntry(
                lexicon_id=lexicon_id,
                word="curated",
                normalized_word="curated",
                source_meanings=["整理好的"],
                source_raw="curated",
                default_anchor="整理好的",
            )
        )
        session.commit()
    assert entry_count(world, lexicon_id) == 1
    assert state_ids_in(world, lexicon_id) == set()

    response = world.client.delete(f"/api/lexicons/{lexicon_id}")

    assert response.status_code == 200, response.text
    assert world.client.get(f"/api/lexicons/{lexicon_id}").status_code == 404


def test_a_lexicon_with_only_an_untouched_queue_placeholder_can_be_deleted(world) -> None:
    """A ``new`` state with no override, review, or schedule is not progress."""
    from app.models import UserWordState

    lexicon_id = world.lexicon("placeholder-lexicon")
    state_id, _ = world.add_word("queued", lexicon_id=lexicon_id)
    with world.session() as session:
        state = session.get(UserWordState, state_id)
        assert state is not None
        state.anchor_override = ""
        session.commit()

    assert state_ids_in(world, lexicon_id) == {state_id}
    response = world.client.delete(f"/api/lexicons/{lexicon_id}")
    assert response.status_code == 200, response.text
    assert world.client.get(f"/api/lexicons/{lexicon_id}").status_code == 404


# --- (d) two-user isolation is unchanged ----------------------------------


def test_another_users_lexicon_stays_404_never_409(two_worlds) -> None:
    """The guard must not leak another user's learning records.

    B's lexicon holds learning state, so a 409 would confirm both that the lexicon
    exists and that it is being studied. Ownership is therefore decided first and
    the answer stays 404, identical to a lexicon that does not exist at all.
    """
    a, b = two_worlds
    b_lexicon_id = b.lexicon("b-studied-lexicon")
    b_state_id, _ = b.add_word("b-word", lexicon_id=b_lexicon_id, status="weak")
    states_before = state_ids_in(b, b_lexicon_id)
    assert states_before, "the 404 case must still exercise the guard's condition"

    response = a.client.delete(f"/api/lexicons/{b_lexicon_id}")

    assert response.status_code == 404, response.text
    assert "学习记录" not in response.text
    assert state_ids_in(b, b_lexicon_id) == states_before
    assert state_snapshot(b, b_state_id)[0] == "weak"
    # B's own access is untouched.
    assert b.client.get(f"/api/lexicons/{b_lexicon_id}").status_code == 200
    assert b.client.get("/api/words").json()["total"] == 1


def test_a_user_may_still_delete_their_own_unstudied_lexicon_while_the_neighbour_studies(
    two_worlds,
) -> None:
    """One user's refusal must not depend on another user's records."""
    a, b = two_worlds
    b_lexicon_id = b.lexicon("b-studied-lexicon")
    b.add_word("b-word", lexicon_id=b_lexicon_id, status="weak")

    created = a.client.post("/api/lexicons", json={"name": "A 的空词库"})
    assert created.status_code == 201
    a_empty_id = created.json()["id"]

    assert a.client.delete(f"/api/lexicons/{a_empty_id}").status_code == 200
    assert state_ids_in(b, b_lexicon_id) != set()
    assert b.client.get(f"/api/lexicons/{b_lexicon_id}").status_code == 200
