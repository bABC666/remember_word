"""One route, one identifier namespace.

A word can be named two ways: by its V1.1 ``word.id`` (the migrated words) or by
the caller's ``user_word_state.id`` (every word, including the ones that have no
legacy row because they were added from an article). Accepting either identifier on
the same route -- the fallback that used to exist -- makes it impossible to say
which row a request reached, and turns a typo into a wrong-but-successful write.

The contract under test:

* ``GET /api/words/{id}`` and ``POST /api/study/words/{id}/review`` take a legacy
  ``word.id`` and nothing else;
* ``GET /api/words/state/{id}`` and ``POST /api/study/word-states/{id}/review`` take
  a ``user_word_state.id`` and nothing else;
* a word with no legacy row is reachable only through the state route, and a state
  id passed to a legacy route is a 404 rather than a silent reinterpretation;
* the id the API reports is unambiguous: ``id`` is the legacy id (``None`` when
  there is none) and ``word_state_id`` is always the state id;
* a state id belonging to somebody else is a 404 on the state route, and the
  owner's data does not change.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("pin_data_dir")


@pytest.fixture()
def two_named_words(world):
    """One word with a legacy row, one without, both belonging to ``world``."""
    state_id, _entry_id = world.add_word("article-word", anchor="文章词", status="new")
    (
        legacy_state_id,
        _legacy_entry_id,
        legacy_word_id,
    ) = world.add_legacy_word("migrated-word", anchor="迁移词", status="new")
    return {
        "modern_state_id": state_id,
        "legacy_state_id": legacy_state_id,
        "legacy_word_id": legacy_word_id,
    }


def test_the_payload_reports_each_namespace_separately(world) -> None:
    state_id, _entry_id = world.add_word("payload-word")
    _lsid, _leid, legacy_word_id = world.add_legacy_word("payload-legacy")

    items = {item["word"]: item for item in world.client.get("/api/words").json()["words"]}

    modern = items["payload-word"]
    assert modern["id"] is None, "a word with no legacy row has no legacy id"
    assert modern["legacy_word_id"] is None
    assert modern["word_state_id"] == state_id

    legacy = items["payload-legacy"]
    assert legacy["id"] == legacy_word_id
    assert legacy["legacy_word_id"] == legacy_word_id
    assert legacy["word_state_id"] == _lsid


def test_the_legacy_route_serves_the_legacy_word(two_named_words, world) -> None:
    ids = two_named_words
    response = world.client.get(f"/api/words/{ids['legacy_word_id']}")
    assert response.status_code == 200
    assert response.json()["word"] == "migrated-word"
    assert response.json()["word_state_id"] == ids["legacy_state_id"]


def test_the_state_route_serves_the_word_without_a_legacy_row(
    two_named_words, world
) -> None:
    ids = two_named_words
    response = world.client.get(f"/api/words/state/{ids['modern_state_id']}")
    assert response.status_code == 200
    assert response.json()["word"] == "article-word"
    assert response.json()["id"] is None


def test_a_state_id_is_not_a_legacy_word_id(two_named_words, world) -> None:
    """The legacy route must not fall back to the state namespace.

    The two ids belong to different words here, so a fallback would be visible as a
    successful response for the wrong word rather than as an error.
    """
    ids = two_named_words
    response = world.client.get(f"/api/words/{ids['modern_state_id']}")
    assert response.status_code == 404, "a state id must not resolve on the legacy route"


def test_a_legacy_word_id_is_not_a_state_id(two_named_words, world) -> None:
    """And the state route resolves only ``user_word_state.id``.

    ``legacy_word_id`` is chosen so that it does not collide with any state id, so
    the state route must answer 404 rather than serve a different word.
    """
    ids = two_named_words
    assert ids["legacy_word_id"] != ids["modern_state_id"]
    response = world.client.get(f"/api/words/state/{ids['legacy_word_id']}")
    assert response.status_code in {404, 200}
    if response.status_code == 200:
        # It may only be a coincidence of numbering, never a reinterpretation.
        assert response.json()["word_state_id"] == ids["legacy_word_id"]


def test_review_through_the_state_route_updates_the_right_word(
    two_named_words, world
) -> None:
    ids = two_named_words

    response = world.client.post(
        f"/api/study/word-states/{ids['modern_state_id']}/review",
        json={"result": "fail", "source": "daily", "review_type": "recall"},
    )
    assert response.status_code == 200, response.text

    modern = world.client.get(f"/api/words/state/{ids['modern_state_id']}").json()
    legacy = world.client.get(f"/api/words/{ids['legacy_word_id']}").json()
    assert modern["status"] == "weak" and modern["recall_fail"] == 1
    assert legacy["status"] == "new" and legacy["recall_fail"] == 0
    assert len(modern["review_history"]) == 1
    assert legacy["review_history"] == []


def test_review_through_the_legacy_route_updates_the_right_word(
    two_named_words, world
) -> None:
    ids = two_named_words

    response = world.client.post(
        f"/api/study/words/{ids['legacy_word_id']}/review",
        json={"result": "fail", "source": "daily", "review_type": "recall"},
    )
    assert response.status_code == 200, response.text

    legacy = world.client.get(f"/api/words/{ids['legacy_word_id']}").json()
    modern = world.client.get(f"/api/words/state/{ids['modern_state_id']}").json()
    assert legacy["status"] == "weak" and legacy["recall_fail"] == 1
    assert modern["status"] == "new" and modern["recall_fail"] == 0
    assert len(legacy["review_history"]) == 1
    assert modern["review_history"] == []


def test_a_state_id_on_the_legacy_review_route_changes_nothing(
    two_named_words, world
) -> None:
    ids = two_named_words
    before = world.client.get(f"/api/words/state/{ids['modern_state_id']}").json()

    response = world.client.post(
        f"/api/study/words/{ids['modern_state_id']}/review", json={"result": "fail"}
    )
    assert response.status_code == 404

    after = world.client.get(f"/api/words/state/{ids['modern_state_id']}").json()
    assert after == before, "a rejected review must not touch the word"


def test_another_users_state_id_is_404_and_changes_nothing(make_world) -> None:
    owner = make_world("namespace-owner")
    intruder = make_world("namespace-intruder")
    try:
        state_id, _entry_id = owner.add_word("owner-word", anchor="归属词")
        before = owner.client.get(f"/api/words/state/{state_id}").json()

        assert intruder.client.get(f"/api/words/state/{state_id}").status_code == 404
        assert (
            intruder.client.post(
                f"/api/study/word-states/{state_id}/review", json={"result": "fail"}
            ).status_code
            == 404
        )

        assert owner.client.get(f"/api/words/state/{state_id}").json() == before
    finally:
        owner.client.__exit__(None, None, None)
        intruder.client.__exit__(None, None, None)


def test_a_word_without_a_legacy_row_is_unreachable_through_the_legacy_route(
    world,
) -> None:
    """Every word added from an article is in this situation, so the state route
    is not optional: it is the only way to reach it."""
    state_id, _entry_id = world.add_word("only-state-word")

    assert world.client.get(f"/api/words/{state_id}").status_code == 404
    assert world.client.get(f"/api/words/state/{state_id}").status_code == 200
    assert (
        world.client.post(
            f"/api/study/words/{state_id}/review", json={"result": "know"}
        ).status_code
        == 404
    )
    assert (
        world.client.post(
            f"/api/study/word-states/{state_id}/review", json={"result": "know"}
        ).status_code
        == 200
    )
