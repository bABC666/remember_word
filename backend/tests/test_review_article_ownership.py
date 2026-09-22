"""A review may only reference an article the same user owns.

``POST /api/study/words/{id}/review`` and ``POST /api/study/word-states/{id}/review``
accept an optional ``article_id`` (the reading flow records which article the review
came from). Nothing checked who owned that article, so a caller could attach their
review to somebody else's article id.

The check has to happen **before the first write**. A rejected request that had
already moved the word's status, counters and schedule would be a silent partial
success, and "404 and nothing changed" is the only acceptable outcome.

These tests assert the 404 *and* that the word's state, the review table and the
schedule are untouched, for both id namespaces.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("pin_data_dir")


def snapshot(client, state_id: int) -> dict:
    """Everything a review could change, as the API reports it."""
    return client.get(f"/api/words/state/{state_id}").json()


@pytest.fixture()
def review_pair(make_world):
    """Owner with a word (both namespaces) and an article; intruder with an article."""
    owner = make_world("article-owner")
    intruder = make_world("article-intruder")
    state_id, _entry_id = owner.add_word("owned-word", anchor="自己的词")
    (
        legacy_state_id,
        _leid,
        legacy_word_id,
    ) = owner.add_legacy_word("owned-legacy", anchor="旧词")
    owner_article = owner.add_article(title="Owner article")
    intruder_article = intruder.add_article(title="Intruder article")
    yield {
        "owner": owner,
        "intruder": intruder,
        "state_id": state_id,
        "legacy_state_id": legacy_state_id,
        "legacy_word_id": legacy_word_id,
        "owner_article": owner_article,
        "intruder_article": intruder_article,
    }
    owner.client.__exit__(None, None, None)
    intruder.client.__exit__(None, None, None)


@pytest.mark.parametrize("namespace", ["state", "legacy"])
def test_a_review_cannot_reference_another_users_article(review_pair, namespace: str) -> None:
    pair = review_pair
    owner = pair["owner"]

    if namespace == "state":
        path = f"/api/study/word-states/{pair['state_id']}/review"
        observed = pair["state_id"]
    else:
        path = f"/api/study/words/{pair['legacy_word_id']}/review"
        observed = pair["legacy_state_id"]

    before = snapshot(owner.client, observed)

    response = owner.client.post(
        path,
        json={
            "result": "fail",
            "source": "reading",
            "review_type": "context_recall",
            "article_id": pair["intruder_article"],
        },
    )

    assert response.status_code == 404, response.text
    after = snapshot(owner.client, observed)
    assert after == before, "the rejected review changed the word"
    assert after["status"] == before["status"]
    assert after["recall_fail"] == before["recall_fail"]
    assert after["next_review_at"] == before["next_review_at"]
    assert after["review_history"] == before["review_history"], (
        "no review event may have been written"
    )


@pytest.mark.parametrize("namespace", ["state", "legacy"])
def test_a_review_of_ones_own_article_still_works(review_pair, namespace: str) -> None:
    """The ownership check must not break the legitimate reading flow."""
    pair = review_pair
    owner = pair["owner"]
    if namespace == "state":
        path = f"/api/study/word-states/{pair['state_id']}/review"
        observed = pair["state_id"]
    else:
        path = f"/api/study/words/{pair['legacy_word_id']}/review"
        observed = pair["legacy_state_id"]

    response = owner.client.post(
        path,
        json={
            "result": "fail",
            "source": "reading",
            "review_type": "context_recall",
            "article_id": pair["owner_article"],
        },
    )

    assert response.status_code == 200, response.text
    after = snapshot(owner.client, observed)
    assert after["status"] == "weak"
    assert after["recall_fail"] == 1
    assert len(after["review_history"]) == 1
    assert after["review_history"][0]["article_id"] == pair["owner_article"]


def test_a_nonexistent_article_id_is_404_and_changes_nothing(review_pair) -> None:
    pair = review_pair
    before = snapshot(pair["owner"].client, pair["state_id"])

    response = pair["owner"].client.post(
        f"/api/study/word-states/{pair['state_id']}/review",
        json={"result": "fail", "article_id": 10_000_000},
    )

    assert response.status_code == 404
    assert snapshot(pair["owner"].client, pair["state_id"]) == before


def test_the_owner_of_the_article_is_not_told_anything(review_pair) -> None:
    """The refusal must not confirm that the article exists.

    The intruder probing another user's article id gets the same answer as for an id
    that does not exist at all -- which is the whole point of answering 404.
    """
    pair = review_pair
    intruder = pair["intruder"]
    state_id, _entry_id = intruder.add_word("intruder-word", anchor="入侵词")

    with_existing = intruder.client.post(
        f"/api/study/word-states/{state_id}/review",
        json={"result": "fail", "article_id": pair["owner_article"]},
    )
    with_missing = intruder.client.post(
        f"/api/study/word-states/{state_id}/review",
        json={"result": "fail", "article_id": 10_000_000},
    )

    assert with_existing.status_code == with_missing.status_code == 404
    assert with_existing.json()["detail"] == with_missing.json()["detail"]
    assert snapshot(intruder.client, state_id)["status"] == "new"


def test_the_schedule_columns_are_unchanged_after_a_rejection(review_pair) -> None:
    """Reading the raw row, not just the API, for the columns a review writes."""
    from sqlalchemy import select

    from app.models import ReviewEvent, UserWordState

    pair = review_pair
    owner = pair["owner"]

    with owner.session() as session:
        before = session.scalar(select(UserWordState).where(UserWordState.id == pair["state_id"]))
        before_row = (before.status, before.next_review_at, before.last_review, before.recall_fail)
        before_events = session.query(ReviewEvent).count()

    response = owner.client.post(
        f"/api/study/word-states/{pair['state_id']}/review",
        json={
            "result": "fail",
            "review_type": "context_recall",
            "article_id": pair["intruder_article"],
        },
    )
    assert response.status_code == 404

    with owner.session() as session:
        after = session.scalar(select(UserWordState).where(UserWordState.id == pair["state_id"]))
        after_row = (after.status, after.next_review_at, after.last_review, after.recall_fail)
        after_events = session.query(ReviewEvent).count()

    assert after_row == before_row
    assert after_events == before_events
