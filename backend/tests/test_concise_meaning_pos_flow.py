"""The per-part-of-speech write and read path: propose, slot, confirm, withhold.

This module covers the service half of the grouping work. The schema half -- columns,
the widened slot index, the citation table, the migration -- is in
``test_concise_meaning_pos.py``.

The four words are the ones the trial record found awkward, and each stands for one
rule rather than being an arbitrary example:

* ``play`` -- three verb senses plus one noun sense. Four values for one word, which the
  old per-word three-slot shape could not store, and two groups that each start at
  position 1.
* ``performance`` -- its only English gloss sits in the page's ``發音`` section, so the
  section heading is *not* evidence for the part of speech and a person had to judge it.
  Its two displayed values also come from the same source line.
* ``mutter`` -- the page carries Danish, Norwegian and Swedish noun senses beside the
  English ones, so a value that does not state its language must not be displayable.
* ``fertiliser`` -- the page has a noun section but no Chinese gloss in it, and the only
  Chinese on the page is a French verb sense. There is nothing to display, and a
  self-authored supplement has to stay a candidate that nobody has approved.

Everything here is synthetic: rows are created through the service, in the suite's
shared test database, and removed again by this module's teardown. Nothing touches
``data/``.
"""

from __future__ import annotations

import pytest

from app.services.concise_meaning import (
    KIND_AI_SUPPLEMENT,
    KIND_DERIVED,
    KIND_SOURCE,
    STATUS_CANDIDATE,
    ConciseMeaningCitationProposal,
    ConciseMeaningProposal,
    ConciseMeaningRefused,
    concise_meaning_dict,
    confirm,
    describe_entry,
    display_refusal_reason,
    display_refusal_reason_staged,
    load_concise_meanings,
    propose,
    reject,
)

#: The lexicons this module created, so its teardown can remove them again. The
#: application database is session-scoped, and an unrelated module asserts a global
#: entry count, so leaving rows behind would fail a test that is not about this.
_created_lexicon_ids: list[int] = []


@pytest.fixture()
def admin(make_world):
    """An active administrator, whose approval is what puts a value on the page.

    Declared here rather than imported from ``test_concise_meaning.py``: a test module
    that imported another module's fixtures would break the moment that module moved or
    was split, and the fixture is three lines.
    """
    value = make_world("pos-flow-admin", role="admin")
    yield value
    value.client.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _remove_synthetic_lexicons():
    _created_lexicon_ids.clear()
    yield
    if not _created_lexicon_ids:
        return
    from sqlalchemy import delete, select

    from app.db import get_session_factory
    from app.models import (
        EntryConciseMeaning,
        EntryConciseMeaningRevision,
        Lexicon,
        LexiconEntry,
    )

    ids = list(dict.fromkeys(_created_lexicon_ids))
    with get_session_factory()() as session:
        entry_ids = list(
            session.scalars(select(LexiconEntry.id).where(LexiconEntry.lexicon_id.in_(ids)))
        )
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
        session.execute(delete(Lexicon).where(Lexicon.id.in_(ids)))
        session.commit()


# --- helpers ------------------------------------------------------------------


def _lexicon(session, name: str, source_type: str):
    from app.models import Lexicon

    lexicon = Lexicon(
        owner_user_id=None,
        name=name,
        description="synthetic lexicon for the part-of-speech flow tests",
        visibility="public",
        source_type=source_type,
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


def _administrator(session, world):
    from app.models import User

    return session.get(User, world.user_id)


def _group(
    text: str,
    *,
    pos: str,
    pos_order: int,
    order: int = 1,
    kind: str = KIND_DERIVED,
    locator: str = "zhwiktionary:7993707:13",
    note: str = "由来源行抽义",
    pos_source: str = "reviewer",
    pos_evidence: str = "zhwiktionary:7993707:13",
    language: str = "en",
    citations=(),
    source_evidence_id: int | None = None,
) -> ConciseMeaningProposal:
    return ConciseMeaningProposal(
        text=text,
        provenance_kind=kind,
        display_order=order,
        source_locator=locator,
        derivation_note=note,
        source_evidence_id=source_evidence_id,
        pos_key=pos,
        pos_label={"verb": "动词", "noun": "名词", "adj": "形容词"}.get(pos, pos),
        pos_order=pos_order,
        pos_source=pos_source,
        pos_evidence_locator=pos_evidence,
        language=language,
        citations=tuple(citations),
    )


def _rows(session, entry_id: int):
    from app.models import EntryConciseMeaning

    return (
        session.query(EntryConciseMeaning)
        .filter_by(lexicon_entry_id=entry_id)
        .order_by(EntryConciseMeaning.id)
        .all()
    )


def _revisions(session, entry_id: int):
    from app.models import EntryConciseMeaningRevision

    return (
        session.query(EntryConciseMeaningRevision)
        .filter_by(lexicon_entry_id=entry_id)
        .order_by(EntryConciseMeaningRevision.id)
        .all()
    )


def _confirm_all(session, entry_id: int, world) -> None:
    administrator = _administrator(session, world)
    for row in _rows(session, entry_id):
        confirm(session, meaning=row, confirmer=administrator)
    session.commit()


# --- play: four values across two groups --------------------------------------


def test_play_carries_four_values_across_two_groups(admin) -> None:
    """The case the old shape could not store, end to end.

    Three verb senses and one noun sense: four values, two groups, and each group's
    positions start at 1. The read path returns them group by group.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-play", "pos-flow-play")
        entry = _entry(session, lexicon, "play", source_raw="play v. 玩；演奏；播放")
        administrator = _administrator(session, admin)

        created = propose(
            session, entry=entry,
            proposals=[
                _group("玩", pos="verb", pos_order=1, order=1,
                       locator="zhwiktionary:7993707:13"),
                _group("演奏", pos="verb", pos_order=1, order=2,
                       locator="zhwiktionary:7993707:14"),
                _group("播放", pos="verb", pos_order=1, order=3,
                       locator="zhwiktionary:7993707:10"),
                _group("剧", pos="noun", pos_order=2, order=1,
                       locator="zhwiktionary:7993707:9"),
            ],
            actor=administrator,
        )
        session.commit()
        assert len(created) == 4

        # Four rows in the store, and none of them visible yet.
        assert load_concise_meanings(session, [entry.id]) == {}

        _confirm_all(session, entry.id, admin)
        displayed = load_concise_meanings(session, [entry.id])[entry.id]

        assert [(row.pos_key, row.display_order) for row in displayed] == [
            ("verb", 1), ("verb", 2), ("verb", 3), ("noun", 1),
        ]
        assert [row.text for row in displayed] == ["玩", "演奏", "播放", "剧"]
        assert [row.pos_order for row in displayed] == [1, 1, 1, 2]


def test_the_same_position_in_two_groups_is_two_slots(admin) -> None:
    """Position 1 belongs to a group, so the noun group may use it too.

    Three different things, which is why the pair has to be the key rather than the
    number: two groups may each use position 1; the same pair twice **in one call** is
    refused before anything is written; and a second call for a live *candidate* at the
    same pair supersedes it, because a draft is meant to be correctable before anybody
    approves it.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-slot", "pos-flow-slot")
        entry = _entry(session, lexicon, "play")
        administrator = _administrator(session, admin)

        propose(session, entry=entry,
                proposals=[_group("玩", pos="verb", pos_order=1, order=1)], actor=administrator)
        propose(session, entry=entry,
                proposals=[_group("剧", pos="noun", pos_order=2, order=1)], actor=administrator)
        session.commit()
        assert sorted(
            (row.pos_key, row.display_order)
            for row in _rows(session, entry.id)
            if row.status != "rejected"
        ) == [("noun", 1), ("verb", 1)]

        with pytest.raises(ConciseMeaningRefused) as error:
            propose(
                session, entry=entry,
                proposals=[
                    _group("玩耍", pos="verb", pos_order=1, order=1),
                    _group("戏耍", pos="verb", pos_order=1, order=1),
                ],
                actor=administrator,
            )
        session.rollback()
        assert "同一个词性组内展示位置不能重复" in str(error.value)

        # A live candidate at the same pair is superseded, not blocked.
        created = propose(session, entry=entry,
                          proposals=[_group("玩耍", pos="verb", pos_order=1, order=1)],
                          actor=administrator)
        session.commit()
        assert len(created) == 1
        superseded = [row for row in _rows(session, entry.id) if row.status == "rejected"]
        assert [row.text for row in superseded] == ["玩"]


def test_a_confirmed_value_must_be_withdrawn_before_its_slot_is_reused(admin) -> None:
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-withdraw", "pos-flow-withdraw")
        entry = _entry(session, lexicon, "play")
        administrator = _administrator(session, admin)
        row = propose(session, entry=entry,
                      proposals=[_group("玩", pos="verb", pos_order=1, order=1)],
                      actor=administrator)[0]
        session.commit()
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()

        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry,
                    proposals=[_group("玩耍", pos="verb", pos_order=1, order=1)],
                    actor=administrator)
        session.rollback()
        assert "已经有一条已确认的释义" in str(error.value)

        reject(session, meaning=row, actor=administrator, note="换成更常用的表述")
        session.commit()
        assert load_concise_meanings(session, [entry.id]) == {}

        propose(session, entry=entry,
                proposals=[_group("玩耍", pos="verb", pos_order=1, order=1)],
                actor=administrator)
        session.commit()
        assert [r.display_order for r in _rows(session, entry.id) if r.status != "rejected"] == [1]


# --- performance: a judged part of speech, and one line used twice ------------


def test_performance_records_a_reviewer_basis_because_the_heading_is_not_one(admin) -> None:
    """``performance``'s only English gloss sits under ``===發音===``.

    The section heading is not a part-of-speech heading, so the honest basis is a
    person's judgement against the gloss line itself -- never ``pos_section``. The
    service requires a position for either basis, so "a person decided" is still
    checkable rather than being an assertion with nothing behind it.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-performance", "pos-flow-performance")
        entry = _entry(session, lexicon, "performance")
        administrator = _administrator(session, admin)

        assert propose(
            session, entry=entry,
            proposals=[_group("表演", pos="noun", pos_order=1, order=1,
                              pos_source="reviewer",
                              pos_evidence="zhwiktionary:8457333:10",
                              locator="zhwiktionary:8457333:10")],
            actor=administrator,
        )[0].pos_source == "reviewer"
        session.commit()

        # A basis with no position is refused, whichever basis it claims to be.
        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry,
                    proposals=[_group("执行", pos="noun", pos_order=1, order=2,
                                      pos_source="reviewer", pos_evidence="")],
                    actor=administrator)
        session.rollback()
        assert "必须记录位置" in str(error.value)


def test_performance_shares_one_source_line_between_two_values(admin) -> None:
    """``表演`` and ``执行`` both come from line 10, and both cite their second source.

    A citation is not a unique key on the position: two values extracted from one line
    is an ordinary thing for a dictionary line to contain. The shared citation is the
    same locator on both rows, which is only expressible because a citation is a row
    with its own ``source_evidence_id`` rather than a field on the value.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-shared-cite", "pos-flow-shared-cite")
        entry = _entry(session, lexicon, "performance")
        administrator = _administrator(session, admin)

        created = propose(
            session, entry=entry,
            proposals=[
                _group("表演", pos="noun", pos_order=1, order=1,
                       locator="zhwiktionary:8457333:10",
                       pos_evidence="zhwiktionary:8457333:10",
                       citations=[ConciseMeaningCitationProposal(
                           citation_locator="wikdict:37", citation_order=1)]),
                _group("执行", pos="noun", pos_order=1, order=2,
                       locator="zhwiktionary:8457333:10",
                       pos_evidence="zhwiktionary:8457333:10",
                       citations=[ConciseMeaningCitationProposal(
                           citation_locator="wikdict:37", citation_order=1)]),
            ],
            actor=administrator,
        )
        session.commit()
        _confirm_all(session, entry.id, admin)

        reported = describe_entry(session, entry)
        shown = load_concise_meanings(session, [entry.id])[entry.id]

    assert [row.text for row in shown] == ["表演", "执行"]
    assert {row.source_locator for row in shown} == {"zhwiktionary:8457333:10"}
    assert [item["citations"][0]["citation_locator"] for item in reported["proposals"]] == [
        "wikdict:37", "wikdict:37",
    ]
    assert len(created) == 2


# --- mutter: the page carries other languages ---------------------------------


def test_mutter_refuses_a_cross_language_group(admin) -> None:
    """``mutter``'s Danish, Norwegian and Swedish noun senses must not become English.

    Two halves. A group that *declares* another language is refused outright. A group
    that simply does not record one is storable -- a reviewer needs something to work
    with -- but ``confirm`` refuses it, and the read path withholds the row even if some
    other writer confirmed it.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-mutter", "pos-flow-mutter")
        entry = _entry(session, lexicon, "mutter")
        administrator = _administrator(session, admin)

        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry,
                    proposals=[_group("妈妈", pos="noun", pos_order=1, language="da")],
                    actor=administrator)
        session.rollback()
        assert "未知的语言标记" in str(error.value)
        assert "其它语言" in str(error.value)

        # "not recorded" is storable as a candidate...
        row = propose(session, entry=entry,
                      proposals=[_group("嘀咕", pos="noun", pos_order=1,
                                        locator="zhwiktionary:8436308:14",
                                        pos_evidence="zhwiktionary:8436308:12",
                                        pos_source="pos_section", language="")],
                      actor=administrator)[0]
        session.commit()
        assert row.language == ""

        # ...but not confirmable, because nothing says it is the English sense.
        with pytest.raises(ConciseMeaningRefused) as error:
            confirm(session, meaning=row, confirmer=administrator)
        session.rollback()
        assert "没有记录语言" in str(error.value)
        assert "language='en'" in str(error.value)

        session.refresh(row)
        assert row.status == STATUS_CANDIDATE, "a refusal must leave the row untouched"
        assert load_concise_meanings(session, [entry.id]) == {}


def test_a_confirmed_row_whose_language_is_not_the_target_is_withheld(admin) -> None:
    """The read path does not trust that confirmation already checked.

    A row written before this rule existed -- or by any writer that skipped the service
    -- can be confirmed and still not be displayable. The conservative answer is to show
    nothing and say why, which the review output reports.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-withheld", "pos-flow-withheld")
        entry = _entry(session, lexicon, "mutter")
        administrator = _administrator(session, admin)
        row = propose(session, entry=entry,
                      proposals=[_group("嘀咕", pos="noun", pos_order=1,
                                        locator="zhwiktionary:8436308:14",
                                        pos_evidence="zhwiktionary:8436308:12",
                                        pos_source="pos_section")],
                      actor=administrator)[0]
        session.commit()
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()
        assert load_concise_meanings(session, [entry.id])[entry.id]

        # Now simulate the row as a pre-rule writer left it: confirmed, no language.
        row.language = ""
        session.commit()
        assert "语言未记录" in display_refusal_reason(row)
        assert load_concise_meanings(session, [entry.id]) == {}

        reported = describe_entry(session, entry)
        assert reported["displayed"] == []
        assert reported["withheld"] == [
            {"id": row.id, "text": "嘀咕", "reason": "语言未记录"}
        ]


# --- fertiliser: nothing to display, and a supplement that stays a candidate --


def test_fertiliser_has_no_source_backed_value_and_its_supplement_is_not_shown(admin) -> None:
    """The page has a noun section but no Chinese gloss in it.

    ``fertiliser``'s English line is an "alternative spelling of" template, and the only
    Chinese on the page belongs to a French verb section. So there is no source value to
    propose, and the candidate that a person or a machine drafts has to stay a candidate
    until somebody approves it -- and it may not carry a source pointer at all.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-fertiliser", "pos-flow-fertiliser")
        entry = _entry(session, lexicon, "fertiliser",
                       source_meanings=[], source_raw="")
        administrator = _administrator(session, admin)

        # There is nothing in the source to quote, so a 'source' claim is refused.
        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry,
                    proposals=[_group("肥料", pos="noun", pos_order=1, kind=KIND_SOURCE,
                                      locator="zhwiktionary:7831922:6")],
                    actor=administrator)
        session.rollback()
        assert "未见于该词的来源原文" in str(error.value)

        supplement = propose(
            session, entry=entry,
            proposals=[_group("肥料", pos="noun", pos_order=1, kind=KIND_AI_SUPPLEMENT,
                              locator="", pos_evidence="zhwiktionary:7831922:3",
                              pos_source="pos_section", note="来源没有中文释义，按词形补充")],
            actor=administrator,
        )[0]
        session.commit()

        assert supplement.status == STATUS_CANDIDATE
        assert supplement.source_locator == ""
        assert load_concise_meanings(session, [entry.id]) == {}, (
            "an unconfirmed supplement is invisible, as a candidate always is"
        )

        # A supplement may not be given a source pointer, by either route.
        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry,
                    proposals=[_group("化肥", pos="noun", pos_order=1, order=2,
                                      kind=KIND_AI_SUPPLEMENT, locator="",
                                      pos_evidence="zhwiktionary:7831922:3",
                                      pos_source="pos_section", note="补充",
                                      citations=[ConciseMeaningCitationProposal(
                                          citation_locator="wikdict:1")])],
                    actor=administrator)
        session.rollback()
        assert "不得携带附加引用" in str(error.value)


def test_an_ai_supplement_with_citations_cannot_be_confirmed(admin) -> None:
    """The cross-table half of the same rule, enforced where the database cannot see it.

    A CHECK constraint can forbid the primary pointer but cannot look at the citation
    table, so a row that acquired a citation before this rule existed has to be refused
    at confirmation and withheld by the read path.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-supp-cite", "pos-flow-supp-cite")
        entry = _entry(session, lexicon, "fertiliser")
        administrator = _administrator(session, admin)
        row = propose(session, entry=entry,
                      proposals=[_group("肥料", pos="noun", pos_order=1,
                                        kind=KIND_AI_SUPPLEMENT, locator="",
                                        pos_evidence="zhwiktionary:7831922:3",
                                        pos_source="pos_section", note="补充")],
                      actor=administrator)[0]
        session.commit()

        from app.models import EntryConciseMeaningCitation

        session.add(EntryConciseMeaningCitation(
            concise_meaning_id=row.id, citation_order=1,
            citation_locator="wikdict:1",
        ))
        session.commit()

        with pytest.raises(ConciseMeaningRefused) as error:
            confirm(session, meaning=row, confirmer=administrator)
        session.rollback()
        assert "带着附加引用" in str(error.value)
        # The reason confirm judged is the same one the read path would give the row
        # once confirmed -- which is the point of there being one rule.
        assert display_refusal_reason_staged(row) == "自拟补充带着来源引用"


# --- a part of speech is never inferred ---------------------------------------


def test_a_candidate_from_before_the_rule_is_not_silently_given_a_part_of_speech(admin) -> None:
    """A row that predates grouping keeps the undetermined state it was written with.

    This is the property the migration's refusal rule protects: nothing in the read or
    write path fills the state in, so such a row stays invisible and unconfirmable until
    a person states the part of speech. It is not shown as "no part of speech": it is not
    shown at all.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-legacy", "pos-flow-legacy")
        entry = _entry(session, lexicon, "play")
        administrator = _administrator(session, admin)

        # Exactly what a pre-0011 writer produced: no part-of-speech columns named.
        from app.models import EntryConciseMeaning

        legacy = EntryConciseMeaning(
            lexicon_entry_id=entry.id,
            display_order=1,
            text="玩",
            provenance_kind=KIND_DERIVED,
            source_locator="zhwiktionary:7993707:13",
            derivation_note="由来源行抽义",
            status=STATUS_CANDIDATE,
            proposed_by_username=administrator.username,
        )
        session.add(legacy)
        session.commit()

        assert (legacy.pos_key, legacy.pos_source) == ("", "none")

        with pytest.raises(ConciseMeaningRefused) as error:
            confirm(session, meaning=legacy, confirmer=administrator)
        session.rollback()
        assert "词性未定" in str(error.value)
        assert "不会替你推断词性" in str(error.value)

        session.refresh(legacy)
        assert legacy.status == STATUS_CANDIDATE
        assert (legacy.pos_key, legacy.pos_source) == ("", "none"), (
            "a refusal must not have written a part of speech on the way out"
        )
        assert load_concise_meanings(session, [entry.id]) == {}
        assert [row.action for row in _revisions(session, entry.id) if row.action == "proposed"] == []

        # Classified on purpose by a person, the same row becomes confirmable.
        legacy.pos_key = "verb"
        legacy.pos_label = "动词"
        legacy.pos_order = 1
        legacy.pos_source = "reviewer"
        legacy.pos_evidence_locator = "zhwiktionary:7993707:13"
        legacy.language = "en"
        session.commit()
        confirm(session, meaning=legacy, confirmer=administrator)
        session.commit()
        assert [row.text for row in load_concise_meanings(session, [entry.id])[entry.id]] == ["玩"]


def test_history_records_the_grouping_that_was_in_force(admin) -> None:
    """The append-only record has to answer "which group was this in, and who moved it".

    Reading only the current row would show the new group with no trace of the old one,
    and moving a value between groups changes what the study page shows.
    """
    with admin.session() as session:
        lexicon = _lexicon(session, "pos-flow-history", "pos-flow-history")
        entry = _entry(session, lexicon, "play")
        administrator = _administrator(session, admin)
        row = propose(session, entry=entry,
                      proposals=[_group("演奏", pos="verb", pos_order=1, order=1)],
                      actor=administrator)[0]
        session.commit()
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()
        reject(session, meaning=row, actor=administrator, note="移到名词组")
        session.commit()

        history = _revisions(session, entry.id)

    assert [item.action for item in history] == ["proposed", "confirmed", "rejected"]
    assert {item.pos_key for item in history} == {"verb"}
    assert {item.pos_order for item in history} == {1}
    assert {item.pos_source for item in history} == {"reviewer"}


# --- the response shape is unchanged -----------------------------------------


def test_the_api_dict_shape_is_unchanged() -> None:
    """This slice is backend-only: the study page's contract must not move.

    The part-of-speech fields are exposed through the review output
    (``concise_meaning_review_dict``), not through the response the study page reads, so
    that the frontend does not have to change in the same step.
    """

    class Row:
        text = "玩"
        display_order = 1
        provenance_kind = KIND_DERIVED
        source_locator = "zhwiktionary:7993707:13"
        source_evidence_id = None
        derivation_note = "由来源游玩意抽义"
        confirmed_by_username = "owner"
        confirmed_at = None

    assert set(concise_meaning_dict(Row())) == {
        "text",
        "display_order",
        "provenance_kind",
        "provenance_label",
        "is_source_verbatim",
        "is_supplement",
        "source_locator",
        "source_evidence_id",
        "derivation_note",
        "confirmed_by",
        "confirmed_at",
    }
