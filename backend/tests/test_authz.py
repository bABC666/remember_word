"""IDOR / authorization matrix.

Two users, each with their own private data, exercise every private endpoint.
The rules under test:

* a user may reach their own resources;
* requesting another user's resource answers **404**, identical to a resource
  that does not exist at all — never 403, which would confirm the id exists;
* modifying another user's resource changes nothing;
* a public system lexicon is readable by every authenticated user, but only an
  admin may change it;
* an anonymous request to any authenticated endpoint answers 401.

The three endpoints the Phase 0 audit flagged as highest risk are asserted
explicitly at the bottom.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("pin_data_dir")


class Content:
    """One user's private data, with ids another user must not reach.

    Two words, because V1.2 has two identifier namespaces and each is addressed by
    its own route: a migrated-style word that has a legacy ``word`` row (addressed by
    ``word.id``) and one added from an article that has none (addressed by
    ``word_state_id``). Neither route falls back to the other, so both are exercised.
    """

    def __init__(self, world) -> None:
        self.world = world
        self.lexicon_id = world.lexicon("private-lexicon")
        self.state_id, self.entry_id = world.add_word("mine", anchor="我的", status="weak")
        (
            self.legacy_state_id,
            self.legacy_entry_id,
            self.legacy_word_id,
        ) = world.add_legacy_word("legacy-mine", anchor="旧的", status="weak")
        self.article_id = world.add_article(
            title="My private article", content="Only I should read this sentence."
        )
        self.batch_id = world.add_import_batch()
        self.review_id = world.add_review(self.state_id, "know")
        self.legacy_review_id = world.add_review(self.legacy_state_id, "know")
        self.lookup_id = self._add_lookup()

    def words_path(self, which: str = "legacy") -> str:
        """The V1.1 route, which takes a legacy ``word.id``."""
        return f"/api/words/{self.legacy_word_id}"

    def state_words_path(self, which: str = "modern") -> str:
        """The explicit route, which takes a ``user_word_state.id``."""
        return f"/api/words/state/{self.state_id}"

    def review_path(self) -> str:
        return f"/api/study/words/{self.legacy_word_id}/review"

    def state_review_path(self) -> str:
        return f"/api/study/word-states/{self.state_id}/review"

    def _add_lookup(self) -> int:
        """A cached lookup on this user's article, without calling the AI."""
        from app.models import ArticleWordLookup

        with self.world.session() as session:
            lookup = ArticleWordLookup(
                article_id=self.article_id,
                surface="sentence",
                normalized_word="sentence",
                meaning="句子",
                context="Only I should read this sentence.",
                source="ai",
            )
            session.add(lookup)
            session.commit()
            session.refresh(lookup)
            return lookup.id


@pytest.fixture()
def pair(two_worlds):
    first, second = two_worlds
    return first, Content(first), second, Content(second)


# --- own access ------------------------------------------------------------


def test_each_user_can_reach_their_own_private_data(pair) -> None:
    a, ca, b, cb = pair
    assert a.client.get(ca.words_path()).status_code == 200
    assert a.client.get(ca.state_words_path()).status_code == 200
    assert b.client.get(cb.words_path()).status_code == 200
    assert b.client.get(cb.state_words_path()).status_code == 200
    assert a.client.get(f"/api/articles/{ca.article_id}").status_code == 200
    assert b.client.get(f"/api/articles/{cb.article_id}").status_code == 200
    assert a.client.get(f"/api/imports/{ca.batch_id}").status_code == 200
    assert b.client.get(f"/api/imports/{cb.batch_id}").status_code == 200


def test_a_user_only_lists_their_own_content(pair) -> None:
    a, ca, b, cb = pair

    def identifiers(client):
        items = client.get("/api/words").json()["words"]
        return (
            {item["legacy_word_id"] for item in items},
            {item["word_state_id"] for item in items},
        )

    legacy_a, states_a = identifiers(a.client)
    legacy_b, states_b = identifiers(b.client)
    assert ca.legacy_word_id in legacy_a and cb.legacy_word_id in legacy_b
    assert ca.legacy_word_id not in legacy_b
    assert cb.legacy_word_id not in legacy_a
    assert ca.state_id in states_a and cb.state_id in states_b
    assert ca.state_id not in states_b
    assert cb.state_id not in states_a

    articles_a = {item["id"] for item in a.client.get("/api/articles").json()}
    articles_b = {item["id"] for item in b.client.get("/api/articles").json()}
    assert ca.article_id in articles_a and ca.article_id not in articles_b
    assert cb.article_id in articles_b and cb.article_id not in articles_a


# --- cross-user reads answer 404 -------------------------------------------


@pytest.mark.parametrize(
    ("template", "attribute"),
    [
        ("/api/words/{}", "legacy_word_id"),
        ("/api/words/state/{}", "state_id"),
        ("/api/articles/{}", "article_id"),
        ("/api/imports/{}", "batch_id"),
    ],
)
def test_get_another_users_resource_is_404(pair, template: str, attribute: str) -> None:
    a, _ca, _b, cb = pair
    other_id = getattr(cb, attribute)

    response = a.client.get(template.format(other_id))
    assert response.status_code == 404, f"{template} leaked {other_id}"

    # Identical to a resource that genuinely does not exist.
    missing = a.client.get(template.format(10_000_000))
    assert missing.status_code == 404
    assert response.json()["detail"] == missing.json()["detail"], (
        "the two 404s must be indistinguishable"
    )


def test_review_history_is_not_exposed_across_users(pair) -> None:
    a, ca, b, cb = pair

    # The legacy word and the article-added word are different entries, so each
    # detail response shows its own history and only its own.
    legacy = a.client.get(ca.words_path()).json()
    own_review_ids = {event["id"] for event in legacy["review_history"]}
    assert ca.legacy_review_id in own_review_ids
    assert cb.review_id not in own_review_ids, "another user's review must never appear"
    assert cb.legacy_review_id not in own_review_ids
    assert ca.review_id not in own_review_ids, (
        "a different word of the same user must not lend its history"
    )

    modern = a.client.get(ca.state_words_path()).json()
    modern_ids = {event["id"] for event in modern["review_history"]}
    assert ca.review_id in modern_ids
    assert ca.legacy_review_id not in modern_ids
    assert cb.review_id not in modern_ids

    # B cannot see A's history at all: neither word is B's.
    assert b.client.get(ca.words_path()).status_code == 404
    assert b.client.get(ca.state_words_path()).status_code == 404


def test_cross_user_writes_change_nothing(pair) -> None:
    a, _ca, b, cb = pair

    before = b.client.get(cb.words_path()).json()
    assert a.client.post(cb.review_path(), json={"result": "fail"}).status_code == 404
    assert a.client.post(cb.state_review_path(), json={"result": "fail"}).status_code == 404
    assert a.client.post(f"/api/articles/{cb.article_id}/complete").status_code == 404
    assert a.client.delete(f"/api/imports/{cb.batch_id}").status_code == 404
    assert a.client.delete(f"/api/lexicons/{cb.lexicon_id}").status_code == 404
    assert a.client.patch(
        f"/api/lexicons/{cb.lexicon_id}", json={"name": "hijacked"}
    ).status_code == 404
    after = b.client.get(cb.words_path()).json()

    assert before == after, "a rejected request must not change the owner's data"


def test_article_children_are_reachable_only_through_their_owner(pair) -> None:
    """Exposure and lookup ownership is derived from the article."""
    a, ca, b, _cb = pair

    assert a.client.post(f"/api/articles/{ca.article_id}/lookup", json={"word": "sentence"}).status_code == 200
    assert b.client.post(f"/api/articles/{ca.article_id}/lookup", json={"word": "sentence"}).status_code == 404
    assert b.client.post(
        f"/api/articles/{ca.article_id}/lookups/{ca.lookup_id}/add-word"
    ).status_code == 404
    assert b.client.post(f"/api/articles/{ca.article_id}/translate").status_code == 404


def test_cross_user_judge_is_rejected(pair) -> None:
    _a, ca, b, _cb = pair
    response = b.client.post(
        f"/api/articles/{ca.article_id}/judge",
        json={"word_state_id": ca.state_id, "user_meaning": "偷看"},
    )
    assert response.status_code == 404


# --- settings --------------------------------------------------------------


def test_user_settings_are_per_user_and_not_addressable(pair) -> None:
    a, _ca, b, _cb = pair

    assert a.client.put("/api/settings", json={"daily_new_words": 3}).status_code == 200
    assert b.client.put("/api/settings", json={"daily_new_words": 9}).status_code == 200

    assert a.client.get("/api/settings").json()["daily_new_words"] == 3
    assert b.client.get("/api/settings").json()["daily_new_words"] == 9


def test_client_supplied_user_id_is_not_a_field_anywhere(pair) -> None:
    """No private endpoint accepts an ownership-deciding user_id."""
    a, ca, b, _cb = pair

    # Sending user_id is simply ignored: the response is still scoped to B.
    response = b.client.get(f"/api/words?user_id={a.user_id}")
    ids = {item["legacy_word_id"] for item in response.json()["words"]}
    assert ca.legacy_word_id not in ids

    response = b.client.get(f"/api/articles?user_id={a.user_id}")
    assert ca.article_id not in {item["id"] for item in response.json()}

    response = b.client.get(f"/api/dashboard?user_id={a.user_id}")
    assert response.status_code == 200


# --- lexicons --------------------------------------------------------------


def test_public_system_lexicon_is_readable_by_every_authenticated_user(pair) -> None:
    a, _ca, b, _cb = pair
    listing_a = a.client.get("/api/lexicons")
    listing_b = b.client.get("/api/lexicons")
    assert listing_a.status_code == 200
    assert listing_b.status_code == 200

    system_ids = {item["id"] for item in listing_a.json() if item["is_system"]}
    assert system_ids, "the migrated system lexicon must be visible"
    assert system_ids <= {item["id"] for item in listing_b.json()}
    for lexicon_id in system_ids:
        assert a.client.get(f"/api/lexicons/{lexicon_id}").status_code == 200
        assert b.client.get(f"/api/lexicons/{lexicon_id}").status_code == 200


def test_private_lexicon_is_invisible_to_other_users(pair) -> None:
    a, ca, b, cb = pair
    assert a.client.get(f"/api/lexicons/{ca.lexicon_id}").status_code == 200
    assert b.client.get(f"/api/lexicons/{ca.lexicon_id}").status_code == 404
    assert b.client.get(f"/api/lexicons/{cb.lexicon_id}").status_code == 200
    assert a.client.get(f"/api/lexicons/{cb.lexicon_id}").status_code == 404


def test_only_admin_may_modify_the_system_lexicon(pair, make_world) -> None:
    a, _ca, _b, _cb = pair
    from app.models import Lexicon

    with a.session() as session:
        system_id = session.query(Lexicon).filter(Lexicon.owner_user_id.is_(None)).first().id

    # A normal user cannot write to the shared lexicon. The system lexicon is
    # public, so its existence is not a secret and refusing the *capability* is
    # honestly a 403 (the spec requires exactly this). Another user's PRIVATE
    # lexicon answers 404 instead, so that existence is never disclosed.
    assert a.client.patch(
        f"/api/lexicons/{system_id}", json={"description": "defaced"}
    ).status_code == 403
    assert a.client.delete(f"/api/lexicons/{system_id}").status_code == 403

    admin = make_world("lexicon-admin", role="admin")
    try:
        # An admin can see the system lexicon, so a refused capability there is a
        # 403 rather than a 404: the resource's existence is not a secret from an
        # admin, only the destructive operation is denied.
        assert admin.client.delete(f"/api/lexicons/{system_id}").status_code == 403
        ok = admin.client.patch(
            f"/api/lexicons/{system_id}", json={"description": "curated by admin"}
        )
        assert ok.status_code == 200, ok.text
        # A system lexicon stays public and cannot be deleted, even by an admin.
        assert ok.json()["visibility"] == "public"
        assert admin.client.delete(f"/api/lexicons/{system_id}").status_code == 403
    finally:
        admin.client.__exit__(None, None, None)


def test_users_can_create_and_use_their_own_lexicons(pair) -> None:
    a, _ca, b, _cb = pair
    created = a.client.post("/api/lexicons", json={"name": "A 的私人词库"})
    assert created.status_code == 201
    new_id = created.json()["id"]
    assert created.json()["owner_user_id"] == a.user_id
    assert created.json()["visibility"] == "private"

    listing_b = {item["id"] for item in b.client.get("/api/lexicons").json()}
    assert new_id not in listing_b

    assert a.client.post(f"/api/lexicons/{new_id}/enable", params={"enabled": False}).status_code == 200
    assert b.client.post(f"/api/lexicons/{new_id}/enable").status_code == 404


def test_new_user_enrols_lazily_without_pregenerating_states(pair) -> None:
    """Joining a public lexicon must not create thousands of learning states."""
    from app.models import UserWordState

    a, _ca, b, _cb = pair
    from app.models import Lexicon

    with a.session() as session:
        system_id = session.query(Lexicon).filter(Lexicon.owner_user_id.is_(None)).first().id

    with b.session() as session:
        before = session.query(UserWordState).filter_by(user_id=b.user_id).count()

    response = b.client.post(f"/api/lexicons/{system_id}/enable")
    assert response.status_code == 200

    with b.session() as session:
        after = session.query(UserWordState).filter_by(user_id=b.user_id).count()
    # Only the fixture's own words (one per namespace) -- joining a lexicon adds
    # nothing, which is the point: a public lexicon of thousands of entries must not
    # pre-generate a row per entry per user.
    assert after == before, f"{after} states after joining, {before} before"
    assert before == 2


# --- admin capability vs ownership -----------------------------------------


def test_capability_endpoints_answer_403_not_404(pair) -> None:
    """403 is reserved for a missing capability, never for someone else's data."""
    a, _ca, _b, _cb = pair
    assert a.client.get("/api/users").status_code == 403
    assert a.client.post(
        "/api/users", json={"username": "x", "password": "long-enough-pw"}
    ).status_code == 403
    assert a.client.patch("/api/users/1", json={"role": "admin"}).status_code == 403
    assert a.client.post("/api/settings/backup").status_code == 403


def test_anonymous_requests_are_rejected_everywhere(pair) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/health").status_code == 200, "health stays public"
        for path in (
            "/api/dashboard",
            "/api/words",
            "/api/study/today",
            "/api/articles",
            "/api/imports",
            "/api/lexicons",
            "/api/settings",
            "/api/settings/onboarding",
            "/api/auth/me",
            "/api/users",
        ):
            assert anonymous.get(path).status_code == 401, path
        assert anonymous.post("/api/study/words/1/review", json={"result": "know"}).status_code == 401
        assert (
            anonymous.post(
                "/api/study/word-states/1/review", json={"result": "know"}
            ).status_code
            == 401
        )
        assert anonymous.get("/api/words/state/1").status_code == 401
        assert anonymous.post("/api/articles/1/complete").status_code == 401
        assert anonymous.post("/api/settings/backup").status_code == 401
        assert anonymous.post(
            "/api/imports", files={"files": ("x.jpg", b"not-an-image", "image/jpeg")}
        ).status_code == 401


# --- the three highest-risk endpoints from the Phase 0 audit ----------------


def test_idor_get_other_users_article(pair) -> None:
    a, _ca, _b, cb = pair
    response = a.client.get(f"/api/articles/{cb.article_id}")
    assert response.status_code == 404
    assert "sentence" not in response.text


def test_idor_get_other_users_word(pair) -> None:
    a, _ca, _b, cb = pair
    for path in (cb.words_path(), cb.state_words_path()):
        response = a.client.get(path)
        assert response.status_code == 404, path
        # No learning data, no review history, not even the word text.
        assert "mine" not in response.text


def test_idor_review_other_users_word(pair) -> None:
    a, _ca, b, cb = pair
    before = b.client.get(cb.words_path()).json()

    for path in (cb.review_path(), cb.state_review_path()):
        response = a.client.post(path, json={"result": "fail"})
        assert response.status_code == 404, path

    after = b.client.get(cb.words_path()).json()
    assert after["status"] == before["status"]
    assert after["recall_fail"] == before["recall_fail"]
    assert after["next_review_at"] == before["next_review_at"]
    assert after["review_history"] == before["review_history"]
