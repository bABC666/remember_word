"""The grouped ``concise_meanings`` contract, on every shared word read path.

``concise_meanings`` used to be a flat list of values. It is now a list of
part-of-speech groups, ordered by ``pos_order``, each carrying the part of speech and
its values in ``display_order``, and each value carrying its primary citation plus
every additional one.

Five routes return a word object, and all five build it through the same
``word_dict_from_view`` + ``entry_short_meanings`` pair, so the shape is structural
rather than five copies that agree today:

* ``GET /api/study/today`` -- the study queue
* ``GET /api/words`` -- the lexicon listing
* ``GET /api/words/state/{state_id}`` -- one word's state
* ``GET /api/words/{word_id}`` -- the legacy detail route, by legacy ``word.id``
* ``GET /api/articles/{article_id}`` -- the article's quiz words
* ``POST /api/articles/{id}/lookups/{lookup_id}/add-word`` -- a looked-up word

The point of testing all of them rather than one is the failure this shape change is
most likely to produce: a route that was missed keeps answering the old list, and a
client cannot tell "one group" from "one value".

The other half is the display gate. Only values a person confirmed **and** that pass
the service's display rule may appear; a word with nothing displayable answers an empty
array, and the server never fills that gap from ``source_meanings``, because then a
client could not tell a reviewed value from an unreviewed one.

All data here is synthetic, in the suite's shared test database, and removed again by
this module's teardown. Nothing touches ``data/``.
"""

from __future__ import annotations

import pytest

from app.services.concise_meaning import (
    KIND_AI_SUPPLEMENT,
    KIND_DERIVED,
    ConciseMeaningCitationProposal,
    ConciseMeaningProposal,
    confirm,
    propose,
)

#: Key sets, pinned. A field added or renamed by accident is a client break, and this
#: is the cheapest place to notice one.
GROUP_KEYS = frozenset(
    {"pos_key", "pos_label", "pos_source", "pos_source_label", "pos_order", "meanings"}
)
MEANING_KEYS = frozenset(
    {
        "text",
        "display_order",
        "provenance_kind",
        "provenance_label",
        "is_source_verbatim",
        "is_supplement",
        "source_locator",
        "source_evidence_id",
        "citations",
        "derivation_note",
        "confirmed_by",
        "confirmed_at",
    }
)
CITATION_KEYS = frozenset({"citation_order", "citation_locator", "source_evidence_id"})

_created_lexicon_ids: list[int] = []
_created_word_ids: list[int] = []
_created_article_ids: list[int] = []


@pytest.fixture(autouse=True)
def _remove_synthetic_rows():
    """Delete what this module created from the suite's shared test database.

    The application database is session-scoped, so rows a test leaves behind are visible
    to every later test -- and an unrelated module asserts a global entry count. Cleanup
    rather than a rename, so this module does not depend on the alphabetical order of
    the suite to be harmless.
    """
    _created_lexicon_ids.clear()
    _created_word_ids.clear()
    _created_article_ids.clear()
    yield
    if not (_created_lexicon_ids or _created_word_ids or _created_article_ids):
        return
    from sqlalchemy import delete, select

    from app.db import get_session_factory
    from app.models import (
        Article,
        ArticleWordExposure,
        ArticleWordLookup,
        EntryConciseMeaning,
        EntryConciseMeaningRevision,
        Lexicon,
        LexiconEntry,
        UserWordState,
        Word,
    )

    lexicon_ids = list(dict.fromkeys(_created_lexicon_ids))
    article_ids = list(dict.fromkeys(_created_article_ids))
    word_ids = list(dict.fromkeys(_created_word_ids))
    with get_session_factory()() as session:
        entry_ids = list(
            session.scalars(
                select(LexiconEntry.id).where(LexiconEntry.lexicon_id.in_(lexicon_ids or [-1]))
            )
        )
        if article_ids:
            session.execute(
                delete(ArticleWordLookup).where(ArticleWordLookup.article_id.in_(article_ids))
            )
            session.execute(
                delete(ArticleWordExposure).where(
                    ArticleWordExposure.article_id.in_(article_ids)
                )
            )
            session.execute(delete(Article).where(Article.id.in_(article_ids)))
        if word_ids:
            session.execute(delete(Word).where(Word.id.in_(word_ids)))
        if entry_ids:
            session.execute(
                delete(EntryConciseMeaningRevision).where(
                    EntryConciseMeaningRevision.lexicon_entry_id.in_(entry_ids)
                )
            )
            session.execute(
                delete(EntryConciseMeaning).where(
                    EntryConciseMeaning.lexicon_entry_id.in_(entry_ids)
                )
            )
            session.execute(
                delete(UserWordState).where(UserWordState.lexicon_entry_id.in_(entry_ids))
            )
        if lexicon_ids:
            session.execute(delete(Lexicon).where(Lexicon.id.in_(lexicon_ids)))
        session.commit()


@pytest.fixture()
def admin(make_world):
    value = make_world("pos-api-admin", role="admin")
    yield value
    value.client.__exit__(None, None, None)


# --- helpers ------------------------------------------------------------------


def _lexicon(session, name: str):
    from app.models import Lexicon

    lexicon = Lexicon(
        owner_user_id=None,
        name=name,
        description="synthetic lexicon for the grouped read contract",
        visibility="public",
        source_type=name,
    )
    session.add(lexicon)
    session.commit()
    session.refresh(lexicon)
    _created_lexicon_ids.append(lexicon.id)
    return lexicon


def _entry(session, lexicon, word: str, *, source_meanings=None, source_raw=None):
    from app.models import LexiconEntry

    entry = LexiconEntry(
        lexicon_id=lexicon.id,
        word=word,
        normalized_word=word.strip().casefold(),
        source_meanings=source_meanings if source_meanings is not None else ["来源释义"],
        source_raw=source_raw if source_raw is not None else f"{word} 来源整行原文",
        sequence=1,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _state(session, user, entry):
    from app.services.userdata import get_or_create_word_state

    return get_or_create_word_state(session, user, entry)


def _legacy_word(session, user, entry, state) -> int:
    """A V1.1 ``word`` row bridged to the entry, so the legacy route can be exercised.

    ``GET /api/words/{word_id}`` addresses a word by the legacy id, and it resolves it
    through ``UserWordState.legacy_word_id`` -- not through ``Word.lexicon_entry_id``.
    Both halves are needed: the bridge column is what an import writes, and the state
    column is what the route reads.
    """
    from app.models import Word

    row = Word(
        lexicon_entry_id=entry.id,
        user_id=user.id,
        word=entry.word,
        source_meanings=list(entry.source_meanings),
        source_raw=entry.source_raw,
    )
    session.add(row)
    session.flush()
    state.legacy_word_id = row.id
    session.commit()
    session.refresh(row)
    _created_word_ids.append(row.id)
    return row.id


def _article(session, user, entries) -> int:
    """An article with the given entries exposed, which is what ``quiz_words`` needs."""
    from app.models import Article, ArticleWordExposure

    article = Article(
        user_id=user.id,
        title="synthetic grouped-contract article",
        content="synthetic",
        target_words=[entry.word for entry in entries],
        completed=True,
    )
    session.add(article)
    session.flush()
    for entry in entries:
        session.add(
            ArticleWordExposure(
                article_id=article.id,
                lexicon_entry_id=entry.id,
                context=f"synthetic context for {entry.word}",
            )
        )
    session.commit()
    _created_article_ids.append(article.id)
    return article.id


def _proposal(
    text: str,
    *,
    pos: str,
    pos_order: int,
    order: int,
    pos_label: str,
    kind: str = KIND_DERIVED,
    locator: str,
    note: str = "由来源行抽义",
    citations=(),
    pos_evidence: str | None = None,
) -> ConciseMeaningProposal:
    """One proposal whose part of speech is a reviewer's judgement.

    ``pos_evidence`` defaults to the primary locator, which is the gloss line the
    reviewer judged. A supplement has no primary locator, so it passes the section
    heading it was classified against instead -- the basis still has to name a position.
    """
    return ConciseMeaningProposal(
        text=text,
        provenance_kind=kind,
        display_order=order,
        source_locator=locator,
        derivation_note=note,
        pos_key=pos,
        pos_label=pos_label,
        pos_order=pos_order,
        pos_source="reviewer",
        pos_evidence_locator=pos_evidence if pos_evidence is not None else locator,
        language="en",
        citations=tuple(citations),
    )


def _shape(word: dict) -> list[dict]:
    """The part of a word's ``concise_meanings`` this module is about.

    Projected rather than compared whole: ``confirmed_at`` is a timestamp, and five
    routes that agreed on the grouping but differed in a timestamp would still be a
    contract break. The grouping, the labels, the order and the texts are what the five
    routes have to agree on.
    """
    return [
        {
            "pos_key": group["pos_key"],
            "pos_label": group["pos_label"],
            "pos_source": group["pos_source"],
            "pos_order": group["pos_order"],
            "texts": [meaning["text"] for meaning in group["meanings"]],
            "orders": [meaning["display_order"] for meaning in group["meanings"]],
        }
        for group in word["concise_meanings"]
    ]


def _word_from(response_body: dict, entry_id: int) -> dict:
    return next(
        item for item in response_body["words"] if item["lexicon_entry_id"] == entry_id
    )


# --- play: the 3 + 1 case ------------------------------------------------------


def _seed_play(session, admin):
    """``play`` with three verb senses and one noun sense, all confirmed."""
    from app.models import User

    lexicon = _lexicon(session, "pos-api-play")
    entry = _entry(session, lexicon, "play", source_raw="play v. 玩；演奏；播放；剧")
    administrator = session.get(User, admin.user_id)
    state = _state(session, administrator, entry)
    rows = propose(
        session, entry=entry,
        proposals=[
            _proposal("玩", pos="verb", pos_label="动词", pos_order=1, order=1,
                      locator="zhwiktionary:7993707:13"),
            _proposal("演奏", pos="verb", pos_label="动词", pos_order=1, order=2,
                      locator="zhwiktionary:7993707:14"),
            _proposal("播放", pos="verb", pos_label="动词", pos_order=1, order=3,
                      locator="zhwiktionary:7993707:10"),
            _proposal("剧", pos="noun", pos_label="名词", pos_order=2, order=1,
                      locator="zhwiktionary:7993707:9"),
        ],
        actor=administrator,
    )
    for row in rows:
        confirm(session, meaning=row, confirmer=administrator)
    session.commit()
    return entry, state, administrator


EXPECTED_PLAY = [
    {
        "pos_key": "verb",
        "pos_label": "动词",
        "pos_source": "reviewer",
        "pos_order": 1,
        "texts": ["玩", "演奏", "播放"],
        "orders": [1, 2, 3],
    },
    {
        "pos_key": "noun",
        "pos_label": "名词",
        "pos_source": "reviewer",
        "pos_order": 2,
        "texts": ["剧"],
        "orders": [1],
    },
]


def test_play_reports_three_verb_senses_before_one_noun_sense(admin) -> None:
    with admin.session() as session:
        _entry_row, state, _administrator_user = _seed_play(session, admin)
        state_id = state.id

    body = admin.client.get(f"/api/words/state/{state_id}").json()

    assert _shape(body) == EXPECTED_PLAY, (
        "four values across two groups, groups by pos_order and values by display_order"
    )
    groups = body["concise_meanings"]
    assert set(groups[0]) == GROUP_KEYS
    assert set(groups[0]["meanings"][0]) == MEANING_KEYS
    assert groups[0]["pos_source_label"] == "人工试判"


def test_every_shared_word_read_path_answers_the_same_grouped_shape(admin) -> None:
    """The five routes that return a word object, on the same word, at the same time.

    A route missed by the shape change would keep answering the old flat list, and one
    group would then look exactly like one value to a client.
    """
    with admin.session() as session:
        entry, state, administrator = _seed_play(session, admin)
        state_id = state.id
        entry_id = entry.id
        legacy_word_id = _legacy_word(session, administrator, entry, state)
        article_id = _article(session, administrator, [entry])

    study = admin.client.get("/api/study/today")
    assert study.status_code == 200, study.text
    assert _shape(_word_from(study.json(), entry_id)) == EXPECTED_PLAY

    listing = admin.client.get("/api/words")
    assert listing.status_code == 200, listing.text
    assert _shape(_word_from(listing.json(), entry_id)) == EXPECTED_PLAY

    single = admin.client.get(f"/api/words/state/{state_id}")
    assert single.status_code == 200, single.text
    assert _shape(single.json()) == EXPECTED_PLAY

    legacy = admin.client.get(f"/api/words/{legacy_word_id}")
    assert legacy.status_code == 200, legacy.text
    assert _shape(legacy.json()) == EXPECTED_PLAY

    article = admin.client.get(f"/api/articles/{article_id}")
    assert article.status_code == 200, article.text
    quiz = next(
        item for item in article.json()["quiz_words"] if item["lexicon_entry_id"] == entry_id
    )
    assert _shape(quiz) == EXPECTED_PLAY


def test_the_article_add_word_endpoint_reports_the_same_contract(admin) -> None:
    """The sixth call site, on a word it creates for the private lexicon.

    That word is new, so it has nothing confirmed and the answer is an empty array --
    what this asserts is that the *shape* is the same one, not a stale flat list and not
    a missing key. Whether it has values is the next test's subject.
    """
    from app.models import ArticleWordLookup

    with admin.session() as session:
        entry, _state_row, administrator = _seed_play(session, admin)
        article_id = _article(session, administrator, [entry])
        lookup = ArticleWordLookup(
            article_id=article_id,
            surface="shorthand",
            normalized_word="shorthand",
            meaning="速记",
            source="ai",
        )
        session.add(lookup)
        session.commit()
        lookup_id = lookup.id

    response = admin.client.post(
        f"/api/articles/{article_id}/lookups/{lookup_id}/add-word",
    )
    assert response.status_code == 200, response.text
    word = response.json()["word"]
    assert isinstance(word["concise_meanings"], list)
    assert word["concise_meanings"] == [], (
        "a freshly created private entry has nothing confirmed"
    )


# --- performance: a shared line and two sources --------------------------------


def test_performance_reports_the_primary_and_every_additional_citation(admin) -> None:
    """One value may rest on two positions and two sources, and both are reported.

    ``表演`` and ``执行`` also come from the *same* zh.wiktionary line, which is ordinary
    for a dictionary line -- so a citation is not a unique key on the position, and the
    per-value citation list is what distinguishes them.
    """
    with admin.session() as session:
        from app.models import User

        lexicon = _lexicon(session, "pos-api-performance")
        entry = _entry(session, lexicon, "performance",
                       source_raw="performance n. 表演；演出；执行；完成")
        administrator = session.get(User, admin.user_id)
        state = _state(session, administrator, entry)
        session.commit()
        rows = propose(
            session, entry=entry,
            proposals=[
                _proposal(
                    "表演", pos="noun", pos_label="名词", pos_order=1, order=1,
                    locator="zhwiktionary:8457333:10",
                    citations=[
                        ConciseMeaningCitationProposal(
                            citation_locator="wikdict:37", citation_order=1
                        ),
                        ConciseMeaningCitationProposal(
                            citation_locator="zhwiktionary:8457333:11", citation_order=2
                        ),
                    ],
                ),
                _proposal(
                    "执行", pos="noun", pos_label="名词", pos_order=1, order=2,
                    locator="zhwiktionary:8457333:10",
                    citations=[
                        ConciseMeaningCitationProposal(
                            citation_locator="wikdict:37", citation_order=1
                        )
                    ],
                ),
            ],
            actor=administrator,
        )
        for row in rows:
            confirm(session, meaning=row, confirmer=administrator)
        session.commit()
        state_id = state.id
        entry_id = entry.id

    body = admin.client.get(f"/api/words/state/{state_id}").json()
    groups = body["concise_meanings"]

    assert [group["pos_key"] for group in groups] == ["noun"], "one group, two values"
    meanings = groups[0]["meanings"]
    assert [meaning["text"] for meaning in meanings] == ["表演", "执行"]

    first, second = meanings
    assert first["source_locator"] == "zhwiktionary:8457333:10", "the primary citation"
    assert [item["citation_locator"] for item in first["citations"]] == [
        "wikdict:37",
        "zhwiktionary:8457333:11",
    ], "every additional citation, in order"
    assert [item["citation_order"] for item in first["citations"]] == [1, 2]
    assert all(set(item) == CITATION_KEYS for item in first["citations"])

    # Two values may share a position, so the position is not what identifies a value.
    assert second["source_locator"] == first["source_locator"]
    assert [item["citation_locator"] for item in second["citations"]] == ["wikdict:37"]

    # And on the other routes, the same citations.
    listing = admin.client.get("/api/words").json()
    listed = _word_from(listing, entry_id)
    assert listed["concise_meanings"][0]["meanings"][0]["citations"] == first["citations"]


def test_a_supplement_reports_no_citation_on_any_route(admin) -> None:
    """``ai_supplement`` may not carry a source pointer, so its citation list is empty.

    The primary locator is forbidden for a supplement by a CHECK constraint and the
    citation list by the service, so a client sees both empty together and can present
    the value as what it is rather than as a quotation.
    """
    with admin.session() as session:
        from app.models import User

        lexicon = _lexicon(session, "pos-api-supplement")
        entry = _entry(session, lexicon, "fertiliser", source_meanings=[], source_raw="")
        administrator = session.get(User, admin.user_id)
        state = _state(session, administrator, entry)
        session.commit()
        row = propose(
            session, entry=entry,
            proposals=[_proposal(
                "肥料", pos="noun", pos_label="名词", pos_order=1, order=1,
                kind=KIND_AI_SUPPLEMENT, locator="",
                pos_evidence="zhwiktionary:7831922:3",
                note="来源没有中文释义，按词形补充",
            )],
            actor=administrator,
        )[0]
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()
        state_id = state.id

    meanings = admin.client.get(f"/api/words/state/{state_id}").json()["concise_meanings"]
    value = meanings[0]["meanings"][0]

    assert value["is_supplement"] is True
    assert value["is_source_verbatim"] is False
    assert value["source_locator"] == ""
    assert value["source_evidence_id"] is None
    assert value["citations"] == []


# --- nothing displayable is not a licence to fill the gap ----------------------


def test_an_unconfirmed_value_is_not_leaked_and_the_gap_is_not_filled(admin) -> None:
    """A candidate, and a confirmed value the gate refuses, are both invisible.

    The second is the interesting one: it is ``confirmed`` in the store, so a read path
    that only filtered on status would show it. The server must answer an empty array
    rather than fall back to ``source_meanings``, because a client cannot then tell a
    reviewed value from an unreviewed one.
    """
    with admin.session() as session:
        from app.models import User

        lexicon = _lexicon(session, "pos-api-gate")
        entry = _entry(session, lexicon, "mutter",
                       source_meanings=["嘀咕；嘟哝"], source_raw="mutter n. 嘀咕；嘟哝")
        administrator = session.get(User, admin.user_id)
        state = _state(session, administrator, entry)

        # Confirmed while it did record the target language, then left without one --
        # which is exactly the shape a row written before the rule existed has, and the
        # only way to reach the read path's conservative branch: ``confirm`` refuses a
        # value that declares no language, so the service cannot produce this state.
        no_language = propose(
            session, entry=entry,
            proposals=[_proposal("嘀咕", pos="noun", pos_label="名词", pos_order=1,
                                 order=1, locator="zhwiktionary:8436308:14")],
            actor=administrator,
        )[0]
        confirm(session, meaning=no_language, confirmer=administrator)
        no_language.language = ""
        session.commit()

        # And a plain candidate, which was never visible in the first place.
        propose(
            session, entry=entry,
            proposals=[_proposal("嘟囔", pos="verb", pos_label="动词", pos_order=2,
                                 order=1, locator="zhwiktionary:8436308:21")],
            actor=administrator,
        )
        session.commit()
        state_id = state.id
        entry_id = entry.id

    body = admin.client.get(f"/api/words/state/{state_id}").json()
    assert body["concise_meanings"] == [], (
        "a confirmed value that fails the gate is withheld, not shown"
    )
    assert body["source_meanings"] == ["嘀咕；嘟哝"], (
        "the source default is still reported, untouched and in its own field"
    )
    assert body["source_raw"] == "mutter n. 嘀咕；嘟哝"

    listing = admin.client.get("/api/words").json()
    listed = _word_from(listing, entry_id)
    assert listed["concise_meanings"] == []
    assert listed["source_meanings"] == ["嘀咕；嘟哝"]


def test_nothing_displayable_is_an_empty_array_on_every_route(admin) -> None:
    """``fertiliser``'s position: a page with a noun section and no Chinese gloss in it.

    Nothing was proposed, so nothing is displayable, and every route answers ``[]`` --
    the same answer as a word whose values were all refused. That is the documented
    fallback signal, and it is why it must never be filled in server-side.
    """
    with admin.session() as session:
        from app.models import User

        lexicon = _lexicon(session, "pos-api-empty")
        entry = _entry(session, lexicon, "fertiliser", source_meanings=[], source_raw="")
        administrator = session.get(User, admin.user_id)
        state = _state(session, administrator, entry)
        session.commit()
        state_id = state.id
        entry_id = entry.id
        legacy_word_id = _legacy_word(session, administrator, entry, state)
        article_id = _article(session, administrator, [entry])

    assert admin.client.get(f"/api/words/state/{state_id}").json()["concise_meanings"] == []
    assert admin.client.get(f"/api/words/{legacy_word_id}").json()["concise_meanings"] == []
    assert _word_from(admin.client.get("/api/words").json(), entry_id)[
        "concise_meanings"
    ] == []
    assert _word_from(admin.client.get("/api/study/today").json(), entry_id)[
        "concise_meanings"
    ] == []
    article = admin.client.get(f"/api/articles/{article_id}").json()
    quiz = next(
        item for item in article["quiz_words"] if item["lexicon_entry_id"] == entry_id
    )
    assert quiz["concise_meanings"] == []
