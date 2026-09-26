"""The short study meaning: storage, human confirmation, and what reaches a page.

Everything here runs on synthetic data -- a temporary migrated database, a synthetic
system lexicon and synthetic entries. No real source file is imported, no production
database is opened and no migration is applied to one.

The properties under test are the ones the owner's rule for this round depends on:

* the source's own text is **never** overwritten by a short display value;
* a value nobody confirmed **cannot** reach an API response, by construction rather
  than by convention;
* a simplified display value and the traditional source text can coexist, with the
  source still readable;
* a word with no confirmed short meaning falls back to the source meanings exactly as
  before, and the response says so with an empty list rather than by silently
  substituting something unreviewed;
* two accounts sharing one public lexicon see the same short meanings and keep their
  own learning state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.concise_meaning import (
    KIND_AI_SUPPLEMENT,
    KIND_DERIVED,
    KIND_SOURCE,
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    ConciseMeaningProposal,
    ConciseMeaningRefused,
    confirm,
    describe_entry,
    load_concise_meanings,
    propose,
    reject,
)

ADMIN_PASSWORD = "test-password-123"

#: System lexicons this module created, so its teardown can remove them again.
_created_lexicon_ids: list[int] = []


# --- fixtures and helpers ----------------------------------------------------


@pytest.fixture(autouse=True)
def _remove_synthetic_lexicons():
    """Delete what this module created from the suite's shared application database.

    The application database is session-scoped, so rows a test leaves behind are
    visible to every later test. ``tests/test_import_flow.py`` asserts a **global**
    ``LexiconEntry`` count of zero, and this module's file name sorts before it, so
    without this teardown the leftover entries would fail an unrelated test. Cleanup
    rather than a rename: a test module should not depend on the alphabetical order
    of the suite to be harmless.

    Deletion runs in foreign-key order, because ``public_import_run.target_lexicon_id``
    and the artifact references are ``ON DELETE RESTRICT`` by design.
    """
    _created_lexicon_ids.clear()
    yield
    if not _created_lexicon_ids:
        return
    _delete_created_lexicons()


def _delete_created_lexicons() -> None:
    from sqlalchemy import delete, select

    from app.db import get_session_factory
    from app.models import (
        EntryConciseMeaning,
        EntryConciseMeaningRevision,
        EntrySourceEvidence,
        Lexicon,
        LexiconEntry,
        PublicImportRun,
        PublicImportRunSource,
        ReviewEvent,
        SourceArtifact,
        UserWordState,
    )

    lexicon_ids = list(dict.fromkeys(_created_lexicon_ids))
    with get_session_factory()() as session:
        entry_ids = list(session.scalars(
            select(LexiconEntry.id).where(LexiconEntry.lexicon_id.in_(lexicon_ids))
        ))
        run_ids = list(session.scalars(
            select(PublicImportRun.id).where(
                PublicImportRun.target_lexicon_id.in_(lexicon_ids)
            )
        ))
        artifact_ids = set(session.scalars(
            select(EntrySourceEvidence.source_artifact_id).where(
                EntrySourceEvidence.lexicon_entry_id.in_(entry_ids or [-1])
            )
        )) if entry_ids else set()
        if run_ids:
            artifact_ids |= set(session.scalars(
                select(PublicImportRunSource.source_artifact_id).where(
                    PublicImportRunSource.import_run_id.in_(run_ids)
                )
            ))
        if entry_ids or run_ids or artifact_ids:
            session.execute(delete(EntrySourceEvidence).where(
                EntrySourceEvidence.lexicon_entry_id.in_(entry_ids or [-1])
            ))
        if run_ids:
            session.execute(delete(EntrySourceEvidence).where(
                EntrySourceEvidence.import_run_id.in_(run_ids)
            ))
            session.execute(delete(PublicImportRunSource).where(
                PublicImportRunSource.import_run_id.in_(run_ids)
            ))
            session.execute(delete(PublicImportRun).where(
                PublicImportRun.id.in_(run_ids)
            ))
        if artifact_ids:
            session.execute(delete(SourceArtifact).where(
                SourceArtifact.id.in_(artifact_ids)
            ))
        if entry_ids:
            session.execute(delete(ReviewEvent).where(
                ReviewEvent.lexicon_entry_id.in_(entry_ids)
            ))
            session.execute(delete(EntryConciseMeaningRevision).where(
                EntryConciseMeaningRevision.lexicon_entry_id.in_(entry_ids)
            ))
            session.execute(delete(EntryConciseMeaning).where(
                EntryConciseMeaning.lexicon_entry_id.in_(entry_ids)
            ))
            session.execute(delete(UserWordState).where(
                UserWordState.lexicon_entry_id.in_(entry_ids)
            ))
        session.execute(delete(Lexicon).where(Lexicon.id.in_(lexicon_ids)))
        session.commit()


@pytest.fixture()
def admin(make_world):
    """An active administrator, whose approval is what puts a value on the page."""
    value = make_world("cm-admin", role="admin")
    yield value
    value.client.__exit__(None, None, None)


@pytest.fixture()
def member(make_world):
    """A normal account, like the second person this round is for."""
    value = make_world("cm-member")
    yield value
    value.client.__exit__(None, None, None)


def _system_lexicon(session, name: str, source_type: str):
    """A system public lexicon. ``uq_lexicon_system`` allows one per source type."""
    from app.models import Lexicon

    lexicon = Lexicon(
        owner_user_id=None,
        name=name,
        description="synthetic lexicon used by the concise-meaning tests",
        visibility="public",
        source_type=source_type,
    )
    session.add(lexicon)
    session.commit()
    session.refresh(lexicon)
    _created_lexicon_ids.append(lexicon.id)
    return lexicon


def _entry(
    session,
    lexicon,
    word: str,
    *,
    source_meanings: list[str] | None = None,
    source_raw: str | None = None,
    anchor: str = "",
    sequence: int | None = 1,
):
    """One lexicon entry carrying a synthetic source default and raw line."""
    from app.models import LexiconEntry

    entry = LexiconEntry(
        lexicon_id=lexicon.id,
        word=word,
        normalized_word=word.strip().casefold(),
        source_meanings=source_meanings if source_meanings is not None else ["来源释义"],
        source_raw=source_raw if source_raw is not None else f"{word} n. 来源整行原文",
        default_anchor=anchor,
        sequence=sequence,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _user(session, world):
    from app.models import User

    return session.get(User, world.user_id)


def _source_snapshot(entry) -> tuple[Any, ...]:
    """Everything this slice promises never to change."""
    return (
        list(entry.source_meanings),
        entry.source_raw,
        entry.default_anchor,
        entry.word,
        entry.normalized_word,
        entry.sequence,
    )


def _proposal(
    text: str,
    *,
    kind: str = KIND_SOURCE,
    order: int = 1,
    locator: str = "primary:2",
    note: str = "",
) -> ConciseMeaningProposal:
    return ConciseMeaningProposal(
        text=text,
        provenance_kind=kind,
        display_order=order,
        source_locator=locator,
        derivation_note=note,
    )


def _rows(session, entry_id: int):
    from app.models import EntryConciseMeaning

    return session.query(EntryConciseMeaning).filter_by(lexicon_entry_id=entry_id).order_by(
        EntryConciseMeaning.id
    ).all()


def _revisions(session, entry_id: int):
    from app.models import EntryConciseMeaningRevision

    return session.query(EntryConciseMeaningRevision).filter_by(
        lexicon_entry_id=entry_id
    ).order_by(EntryConciseMeaningRevision.id).all()


# --- the source is never overwritten -----------------------------------------


def test_proposing_changes_no_source_field(admin) -> None:
    from app.models import LexiconEntry

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-source-a", "cm-test-source-a")
        entry = _entry(session, lexicon, "amber",
                       source_meanings=["琥珀；一种树脂化石", "琥珀色"],
                       source_raw="amber n. 琥珀；琥珀色。原书补充说明。",
                       anchor="树脂")
        before = _source_snapshot(entry)

        created = propose(
            session, entry=entry, proposals=[_proposal("琥珀", order=1)],
            actor=_user(session, admin),
        )
        session.commit()

        after = _source_snapshot(session.get(LexiconEntry, entry.id))

    assert before == after, "a short display value must not touch the source fields"
    assert len(created) == 1
    assert created[0].status == STATUS_CANDIDATE
    # ``default_anchor`` in particular stays empty: the short meaning is not an anchor
    # and putting it there would make "user override beats lexicon default" ambiguous.
    assert after[2] == "树脂"


def test_confirming_changes_no_source_field(admin) -> None:
    from app.models import LexiconEntry

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-source-b", "cm-test-source-b")
        entry = _entry(session, lexicon, "awe", source_meanings=["敬畏；畏惧"])
        before = _source_snapshot(entry)
        row = propose(session, entry=entry, proposals=[_proposal("敬畏")],
                      actor=_user(session, admin))[0]
        confirm(session, meaning=row, confirmer=_user(session, admin))
        session.commit()
        after = _source_snapshot(session.get(LexiconEntry, entry.id))

    assert before == after


def test_a_simplified_value_displays_simplified_while_the_source_keeps_traditional(
    admin,
) -> None:
    """The pilot's P03 case: the source says 農村的, the study page shows 农村的."""
    from app.models import LexiconEntry
    from app.services.concise_meaning import entry_short_meanings

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-hans", "cm-test-hans")
        entry = _entry(session, lexicon, "rural",
                       source_meanings=["農村的"], source_raw="rural adj. 農村的")
        row = propose(
            session, entry=entry,
            proposals=[_proposal("农村的", kind=KIND_DERIVED, locator="zhwiktionary:12",
                                 note="来源为「農村的」，此处为简体转换")],
            actor=_user(session, admin),
        )[0]
        confirm(session, meaning=row, confirmer=_user(session, admin))
        session.commit()

    with admin.session() as session:
        entry = session.get(LexiconEntry, entry.id)
        short = entry_short_meanings(session, [entry.id])[entry.id]

    assert [item["text"] for item in short] == ["农村的"]
    assert short[0]["provenance_kind"] == KIND_DERIVED
    assert short[0]["is_source_verbatim"] is False, (
        "a converted value is not the source's own wording and must not claim to be"
    )
    assert "简体转换" in short[0]["derivation_note"]
    # The traditional source text is still there, untouched and still readable.
    assert entry.source_meanings == ["農村的"], "the source must not be converted"
    assert "農村的" in entry.source_raw


# --- wording carries its provenance ------------------------------------------


@pytest.mark.parametrize(
    ("token", "proposal", "fragment"),
    [
        # A supplement is a human-approved invention; it may not look like a quote.
        ("sup-locator",
         _proposal("看似", kind=KIND_AI_SUPPLEMENT, locator="primary:2", note="补充常见义"),
         "不得指向任何来源位置"),
        ("sup-no-note",
         _proposal("看似", kind=KIND_AI_SUPPLEMENT, locator="", note=""), "必须写明补充理由"),
        ("drv-no-locator",
         _proposal("缩减", kind=KIND_DERIVED, locator="", note="删去词形表"),
         "必须记录来源位置"),
        ("drv-no-note",
         _proposal("缩减", kind=KIND_DERIVED, locator="primary:2", note=""),
         "必须写明改了什么"),
        ("bad-kind",
         _proposal("从没有过的类型", kind="invented", locator="primary:2"),
         "未知的释义来源类型"),
        ("too-long", _proposal("x" * 41, kind=KIND_SOURCE), "太长"),
        ("empty", _proposal("", kind=KIND_SOURCE), "不能为空"),
    ],
)
def test_a_proposal_that_misstates_its_provenance_is_refused(
    admin, token: str, proposal, fragment: str
) -> None:
    with admin.session() as session:
        # ``uq_lexicon_system`` allows one system lexicon per source type, so each
        # parameter needs its own.
        lexicon = _system_lexicon(session, f"cm-kind-{token}", f"cm-test-kind-{token}")
        entry = _entry(session, lexicon, "awe")

        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry, proposals=[proposal], actor=_user(session, admin))
        session.rollback()

        assert fragment in str(error.value)
        assert _rows(session, entry.id) == [], "a refused proposal must write nothing"


def test_the_database_itself_refuses_a_supplement_that_points_at_a_source(admin) -> None:
    """The service check is not the only one: the shape is not representable."""
    from sqlalchemy.exc import IntegrityError

    from app.models import EntryConciseMeaning

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-dbcheck-a", "cm-test-dbcheck-a")
        entry = _entry(session, lexicon, "seemingly")
        session.add(EntryConciseMeaning(
            lexicon_entry_id=entry.id, display_order=1, text="看似",
            provenance_kind=KIND_AI_SUPPLEMENT, source_locator="primary:9",
            derivation_note="补充", status=STATUS_CANDIDATE,
            proposed_by_username="x",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_the_database_itself_refuses_an_unattributed_displayed_value(admin) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models import EntryConciseMeaning

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-dbcheck-b", "cm-test-dbcheck-b")
        entry = _entry(session, lexicon, "menu")
        session.add(EntryConciseMeaning(
            lexicon_entry_id=entry.id, display_order=1, text="菜单",
            provenance_kind=KIND_SOURCE, source_locator="primary:2",
            derivation_note="", status=STATUS_CONFIRMED,
            proposed_by_username="x",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_the_database_itself_refuses_a_long_or_fourth_value(admin) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models import EntryConciseMeaning

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-dbcheck-c", "cm-test-dbcheck-c")
        entry = _entry(session, lexicon, "turnover")
        for order, text in ((1, "x" * 41), (4, "营业额")):
            session.add(EntryConciseMeaning(
                lexicon_entry_id=entry.id, display_order=order, text=text,
                provenance_kind=KIND_SOURCE, source_locator="primary:2",
                derivation_note="", status=STATUS_CANDIDATE,
                proposed_by_username="x",
            ))
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()


def test_at_most_three_meanings_per_call(admin) -> None:
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-slots", "cm-test-slots")
        entry = _entry(session, lexicon, "maximum")
        with pytest.raises(ConciseMeaningRefused) as error:
            propose(
                session, entry=entry,
                proposals=[_proposal(f"义项{i}", order=i) for i in range(1, 5)],
                actor=_user(session, admin),
            )
        session.rollback()
        assert "最多提交 3 个" in str(error.value)

        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry, proposals=[_proposal("越界", order=4)],
                    actor=_user(session, admin))
        session.rollback()
        assert "越界" in str(error.value)


def test_evidence_from_another_entry_is_refused(admin) -> None:
    from app.models import EntrySourceEvidence, PublicImportRun, SourceArtifact

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-evidence", "cm-test-evidence")
        entry = _entry(session, lexicon, "ambassador")
        other = _entry(session, lexicon, "digest", sequence=2)
        artifact = SourceArtifact(
            role="primary", name="synthetic.csv", publisher="synthetic",
            version="1", obtained_at_utc="2026-01-01T00:00:00Z", format="delimited-text-v1",
            mapping_json="{}", mapping_sha256="a" * 64, file_sha256="b" * 64,
            license_id="synthetic", use_scope="local", display_scope="local",
        )
        session.add(artifact)
        session.flush()
        run = PublicImportRun(
            plan_sha256="c" * 64, run_id="run-1", target_lexicon_id=lexicon.id,
            confirmed_by_username="x", status="applied",
        )
        session.add(run)
        session.flush()
        evidence = EntrySourceEvidence(
            lexicon_entry_id=other.id, source_artifact_id=artifact.id,
            import_run_id=run.id, normalized_word=other.normalized_word,
            row_locator=2, field_kind="meaning", sense_key="meaning@2",
            raw_word=other.word, raw_text="消化", evidence_sha256="d" * 64,
            decision="selected", selected_for_default=True,
        )
        session.add(evidence)
        session.commit()

        with pytest.raises(ConciseMeaningRefused) as error:
            propose(
                session, entry=entry,
                proposals=[ConciseMeaningProposal(
                    text="大使", provenance_kind=KIND_SOURCE, display_order=1,
                    source_locator="primary:2", source_evidence_id=evidence.id,
                )],
                actor=_user(session, admin),
            )
        session.rollback()
        assert "不属于词条" in str(error.value)


# --- nothing is shown until a human confirms it ------------------------------


def test_an_unconfirmed_candidate_is_never_loaded(admin) -> None:
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-gate", "cm-test-gate")
        entry = _entry(session, lexicon, "recruit")
        rows = propose(
            session, entry=entry,
            proposals=[_proposal("招募", order=1), _proposal("新兵", order=2)],
            actor=_user(session, admin),
        )
        confirm(session, meaning=rows[0], confirmer=_user(session, admin))
        session.commit()

    with admin.session() as session:
        displayed = load_concise_meanings(session, [entry.id])[entry.id]

    assert [row.text for row in displayed] == ["招募"], (
        "an unconfirmed candidate must not be readable as a display value"
    )
    assert displayed[0].status == STATUS_CONFIRMED
    assert displayed[0].confirmed_by_username == admin.username


def test_a_normal_user_cannot_propose_confirm_or_reject(admin, member) -> None:
    from app.models import EntryConciseMeaning, LexiconEntry

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-authz", "cm-test-authz")
        entry = _entry(session, lexicon, "cast")
        row = propose(session, entry=entry, proposals=[_proposal("投掷")],
                      actor=_user(session, admin))
        confirm(session, meaning=row[0], confirmer=_user(session, admin))
        session.commit()
        meaning_id = row[0].id
        entry_id = entry.id

    with member.session() as session:
        entry = session.get(LexiconEntry, entry_id)
        actor = _user(session, member)
        for action in (
            lambda: propose(session, entry=entry, proposals=[_proposal("铸造", order=2)],
                            actor=actor),
            lambda: confirm(session, meaning=session.get(EntryConciseMeaning, meaning_id),
                            confirmer=actor),
            lambda: reject(session, meaning=session.get(EntryConciseMeaning, meaning_id),
                           actor=actor, note="我想改"),
        ):
            with pytest.raises(ConciseMeaningRefused) as error:
                action()
            session.rollback()
            assert "不是管理员" in str(error.value)

    with admin.session() as session:
        displayed = load_concise_meanings(session, [entry_id])[entry_id]
    assert [row.text for row in displayed] == ["投掷"], "nothing a member did took effect"


def test_confirming_twice_records_one_confirmation(admin) -> None:
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-idem", "cm-test-idem")
        entry = _entry(session, lexicon, "awe")
        row = propose(session, entry=entry, proposals=[_proposal("敬畏")],
                      actor=_user(session, admin))[0]
        administrator = _user(session, admin)
        confirm(session, meaning=row, confirmer=administrator)
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()
        history = _revisions(session, entry.id)

    assert [item.action for item in history] == ["proposed", "confirmed"]


def test_a_confirmed_value_is_replaced_by_withdrawal_not_by_overwrite(admin) -> None:
    from app.models import EntryConciseMeaning

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-replace", "cm-test-replace")
        entry = _entry(session, lexicon, "turnover")
        administrator = _user(session, admin)
        row = propose(session, entry=entry, proposals=[_proposal("营业额")],
                      actor=administrator)[0]
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()
        original_id = row.id
        entry_id = entry.id

        # Overwriting a displayed value directly is refused, with the way forward.
        with pytest.raises(ConciseMeaningRefused) as error:
            propose(session, entry=entry, proposals=[_proposal("周转")],
                    actor=administrator)
        session.rollback()
        assert "不能直接覆盖" in str(error.value)

        reject(session, meaning=session.get(EntryConciseMeaning, original_id),
               actor=administrator, note="负责人复核：应为「周转」")
        session.commit()
        assert load_concise_meanings(session, [entry_id]) == {}, "withdrawal takes it off"

        # The slot is free again, and the old row is still readable.
        fresh = propose(session, entry=entry, proposals=[_proposal("周转")],
                        actor=administrator)[0]
        confirm(session, meaning=fresh, confirmer=administrator)
        session.commit()

        displayed = load_concise_meanings(session, [entry_id])[entry_id]
        old = session.get(EntryConciseMeaning, original_id)
        history = [(item.action, item.text) for item in _revisions(session, entry_id)]

    assert [item.text for item in displayed] == ["周转"]
    assert old.status == STATUS_REJECTED
    assert old.text == "营业额", "the withdrawn value is not erased"
    assert old.confirmed_by_username == admin.username
    assert history == [
        ("proposed", "营业额"),
        ("confirmed", "营业额"),
        ("rejected", "营业额"),
        ("proposed", "周转"),
        ("confirmed", "周转"),
    ]


def test_a_rejection_must_state_a_reason(admin) -> None:
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-note", "cm-test-note")
        entry = _entry(session, lexicon, "menu")
        administrator = _user(session, admin)
        row = propose(session, entry=entry, proposals=[_proposal("选单")],
                      actor=administrator)[0]
        with pytest.raises(ConciseMeaningRefused) as error:
            reject(session, meaning=row, actor=administrator, note="  ")
        session.rollback()
        assert "必须写明理由" in str(error.value)


def test_no_path_updates_or_deletes_the_history(admin) -> None:
    """Fail loudly if a later write ever starts rewriting the decision record."""
    from sqlalchemy import event

    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-append", "cm-test-append")
        entry = _entry(session, lexicon, "reduction")
        administrator = _user(session, admin)

        mutating: list[str] = []

        def watch(_conn, _cursor, statement, _params, _context, _many):
            lowered = " ".join(statement.strip().lower().split())
            if lowered.startswith(("update", "delete")) and (
                "entry_concise_meaning_revision" in lowered
            ):
                mutating.append(lowered[:80])

        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", watch)
        try:
            first = propose(session, entry=entry, proposals=[_proposal("减少")],
                            actor=administrator)[0]
            confirm(session, meaning=first, confirmer=administrator)
            reject(session, meaning=first, actor=administrator, note="改用「缩减」")
            second = propose(session, entry=entry, proposals=[_proposal("缩减")],
                             actor=administrator)[0]
            confirm(session, meaning=second, confirmer=administrator)
            session.commit()
        finally:
            event.remove(bind, "before_cursor_execute", watch)

        assert [item.action for item in _revisions(session, entry.id)] == [
            "proposed", "confirmed", "rejected", "proposed", "confirmed",
        ]
    assert mutating == [], f"the history must be append-only, saw {mutating}"


# --- the read path -----------------------------------------------------------


def test_today_queue_returns_the_confirmed_value_beside_the_source(admin) -> None:
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-api-today", "cm-test-api-today")
        entry = _entry(session, lexicon, "expertise",
                       source_meanings=["专门知识；专家意见；专门技能"],
                       source_raw="expertise n. 专门知识；专家意见")
        from app.services.userdata import get_or_create_word_state

        get_or_create_word_state(session, _user(session, admin), entry)
        administrator = _user(session, admin)
        row = propose(
            session, entry=entry,
            proposals=[
                _proposal("专门知识", order=1),
                _proposal("专长", kind=KIND_DERIVED, order=2,
                          note="拆分来源义项后作简短展示"),
            ],
            actor=administrator,
        )
        confirm(session, meaning=row[0], confirmer=administrator)
        session.commit()
        entry_id = entry.id

    response = admin.client.get("/api/study/today")
    assert response.status_code == 200, response.text
    word = next(item for item in response.json()["words"] if item["lexicon_entry_id"] == entry_id)

    assert [item["text"] for item in word["concise_meanings"]] == ["专门知识"]
    assert word["concise_meanings"][0]["is_source_verbatim"] is True
    # Both source fields survive the short value untouched.
    assert word["source_meanings"] == ["专门知识；专家意见；专门技能"]
    assert word["source_raw"] == "expertise n. 专门知识；专家意见"


def test_a_word_with_no_confirmed_value_reports_an_empty_list(admin) -> None:
    """The fallback is the client's, and the server never fakes a reviewed value."""
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-api-fallback", "cm-test-api-fallback")
        entry = _entry(session, lexicon, "therefore", source_meanings=["所以；因此"],
                       source_raw="therefore adv. 所以；因此")
        from app.services.userdata import get_or_create_word_state

        administrator = _user(session, admin)
        get_or_create_word_state(session, administrator, entry)
        # A candidate exists and is deliberately left unconfirmed.
        propose(session, entry=entry, proposals=[_proposal("所以")], actor=administrator)
        session.commit()
        entry_id = entry.id

    response = admin.client.get("/api/study/today")
    word = next(item for item in response.json()["words"] if item["lexicon_entry_id"] == entry_id)

    assert word["concise_meanings"] == []
    assert word["source_meanings"] == ["所以；因此"]
    assert word["source_raw"] == "therefore adv. 所以；因此"


def test_word_detail_and_list_include_the_confirmed_value(admin) -> None:
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-api-detail", "cm-test-api-detail")
        entry = _entry(session, lexicon, "rural", source_meanings=["農村的"],
                       source_raw="rural adj. 農村的")
        from app.services.userdata import get_or_create_word_state

        administrator = _user(session, admin)
        state = get_or_create_word_state(session, administrator, entry)
        row = propose(
            session, entry=entry,
            proposals=[_proposal("农村的", kind=KIND_DERIVED,
                                 note="来源为「農村的」，此处为简体转换")],
            actor=administrator,
        )[0]
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()
        entry_id = entry.id
        state_id = state.id

    detail = admin.client.get(f"/api/words/state/{state_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [item["text"] for item in body["concise_meanings"]] == ["农村的"]
    assert body["source_meanings"] == ["農村的"]

    listing = admin.client.get("/api/words")
    assert listing.status_code == 200, listing.text
    listed = next(
        item for item in listing.json()["words"] if item["lexicon_entry_id"] == entry_id
    )
    assert [item["text"] for item in listed["concise_meanings"]] == ["农村的"]
    assert listed["source_raw"] == "rural adj. 農村的"


def test_two_users_share_the_short_meaning_and_keep_their_own_state(admin, member) -> None:
    """Content is shared; learning state is not. That is the whole V1.2 split."""
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-two", "cm-test-two")
        entry = _entry(session, lexicon, "altitude", source_meanings=["高度；海拔"],
                       source_raw="altitude n. 高度；海拔；高处")
        from app.services.userdata import get_or_create_word_state

        administrator = _user(session, admin)
        admin_state = get_or_create_word_state(session, administrator, entry)
        member_state = get_or_create_word_state(session, _user(session, member), entry)
        row = propose(
            session, entry=entry,
            proposals=[_proposal("高度", order=1), _proposal("海拔", order=2)],
            actor=administrator,
        )
        for item in row:
            confirm(session, meaning=item, confirmer=administrator)
        session.commit()
        entry_id, admin_state_id, member_state_id = entry.id, admin_state.id, member_state.id

    # The member studies it and marks it weak; the administrator's state is untouched.
    reviewed = member.client.post(
        f"/api/study/word-states/{member_state_id}/review",
        json={"result": "fail", "source": "daily", "review_type": "recall"},
    )
    assert reviewed.status_code == 200, reviewed.text

    for world, state_id in ((admin, admin_state_id), (member, member_state_id)):
        response = world.client.get(f"/api/words/state/{state_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["text"] for item in body["concise_meanings"]] == ["高度", "海拔"], (
            "both accounts must see the same confirmed content"
        )
        assert body["lexicon_entry_id"] == entry_id

    admin_view = admin.client.get(f"/api/words/state/{admin_state_id}").json()
    member_view = member.client.get(f"/api/words/state/{member_state_id}").json()
    assert admin_view["status"] == "new" and member_view["status"] != "new", (
        "one user's review must not move another user's learning state"
    )
    assert admin_view["recall_fail"] == 0 and member_view["recall_fail"] == 1


# --- the administrator CLI ---------------------------------------------------


def _proposal_file(path: Path, entries: list[dict[str, Any]], **extra: Any) -> Path:
    payload = {"format_version": 1, "entries": entries, **extra}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _cli(monkeypatch, password: str = ADMIN_PASSWORD) -> None:
    from app import cli

    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: password)


def test_cli_propose_status_confirm_flow(admin, tmp_path, monkeypatch, capsys) -> None:
    from app import cli

    _cli(monkeypatch)
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-cli", "cm-test-cli")
        entry = _entry(session, lexicon, "awe", source_meanings=["敬畏；畏惧"])
        entry_id = entry.id

    file = _proposal_file(tmp_path / "proposals.json", [{
        "word": "awe",
        "meanings": [{
            "text": "敬畏", "provenance_kind": KIND_SOURCE,
            "source_locator": "zhwiktionary:4",
        }],
    }])

    assert cli.main([
        "concise-meaning", "propose", "--file", str(file),
        "--lexicon", "cm-cli", "--admin", admin.username,
    ]) == 0
    capsys.readouterr()

    # Still invisible: propose writes candidates and nothing else.
    with admin.session() as session:
        assert load_concise_meanings(session, [entry_id]) == {}
        candidate_id = _rows(session, entry_id)[0].id
        assert _rows(session, entry_id)[0].status == STATUS_CANDIDATE

    assert cli.main([
        "concise-meaning", "status", "--lexicon", "cm-cli", "--word", "awe",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    described = report["entries"][0]
    assert described["displayed"] == []
    assert described["proposals"][0]["status"] == STATUS_CANDIDATE
    # The reviewer sees the untouched source fields beside the proposal.
    assert described["source_default_meanings"] == ["敬畏；畏惧"]
    assert described["history"][0]["action"] == "proposed"

    assert cli.main([
        "concise-meaning", "confirm", "--id", str(candidate_id),
        "--lexicon", "cm-cli", "--admin", admin.username,
    ]) == 0
    capsys.readouterr()

    with admin.session() as session:
        displayed = load_concise_meanings(session, [entry_id])[entry_id]
        assert [row.text for row in displayed] == ["敬畏"]
        assert displayed[0].confirmed_by_username == admin.username
        from app.models import LexiconEntry

        entry_row = session.get(LexiconEntry, entry_id)
        assert entry_row.source_meanings == ["敬畏；畏惧"], "the source is still untouched"


def test_cli_refuses_the_wrong_password_and_writes_nothing(admin, tmp_path, monkeypatch, capsys) -> None:
    from app import cli

    _cli(monkeypatch, password="not-the-password")
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-cli-pw", "cm-test-cli-pw")
        entry = _entry(session, lexicon, "awe")
        entry_id = entry.id

    file = _proposal_file(tmp_path / "p.json", [{
        "word": "awe",
        "meanings": [{"text": "敬畏", "provenance_kind": KIND_SOURCE,
                      "source_locator": "primary:2"}],
    }])
    assert cli.main([
        "concise-meaning", "propose", "--file", str(file),
        "--lexicon", "cm-cli-pw", "--admin", admin.username,
    ]) == 1
    capsys.readouterr()

    with admin.session() as session:
        assert _rows(session, entry_id) == []
        assert _revisions(session, entry_id) == []


def test_cli_refuses_a_proposal_file_whose_word_is_not_in_the_lexicon(
    admin, tmp_path, monkeypatch, capsys
) -> None:
    from app import cli

    _cli(monkeypatch)
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-cli-miss", "cm-test-cli-miss")
        entry = _entry(session, lexicon, "awe")
        entry_id = entry.id

    file = _proposal_file(tmp_path / "p.json", [
        {"word": "awe", "meanings": [{"text": "敬畏", "provenance_kind": KIND_SOURCE,
                                     "source_locator": "primary:2"}]},
        {"word": "not-in-this-lexicon", "meanings": [
            {"text": "无", "provenance_kind": KIND_SOURCE, "source_locator": "primary:3"}]},
    ])
    assert cli.main([
        "concise-meaning", "propose", "--file", str(file),
        "--lexicon", "cm-cli-miss", "--admin", admin.username,
    ]) == 1
    capsys.readouterr()

    with admin.session() as session:
        assert _rows(session, entry_id) == [], (
            "a file that names an unknown word must not be written in part"
        )


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"format_version": 2, "entries": []}, "format_version"),
        ({"format_version": 1, "entries": [], "extra": True}, "未知字段"),
        ({"format_version": 1, "entries": [{"word": "awe", "meanings": [],
                                            "note": "x"}]}, "未知字段"),
        ({"format_version": 1, "entries": [{"word": "awe", "meanings": [
            {"text": "敬畏", "provenance_kind": "source", "source_locator": "p:1",
             "kind": "source"}]}]}, "未知字段"),
    ],
)
def test_cli_rejects_a_malformed_proposal_file(tmp_path, payload, fragment) -> None:
    from app import cli

    file = tmp_path / "bad.json"
    file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(cli.ConciseMeaningFileError) as error:
        cli.load_proposal_file(file, lexicon_name="anything")
    assert fragment in str(error.value)


def test_cli_proposal_file_may_not_disagree_about_the_lexicon(tmp_path) -> None:
    from app import cli

    file = _proposal_file(
        tmp_path / "p.json",
        [{"word": "awe", "meanings": [
            {"text": "敬畏", "provenance_kind": KIND_SOURCE, "source_locator": "p:2"}]}],
        lexicon="some-other-lexicon",
    )
    with pytest.raises(cli.ConciseMeaningFileError) as error:
        cli.load_proposal_file(file, lexicon_name="cm-cli")
    assert "不一致" in str(error.value)


def test_cli_display_order_defaults_to_the_position_in_the_list(tmp_path) -> None:
    from app import cli

    file = _proposal_file(tmp_path / "p.json", [{
        "word": "maximum",
        "meanings": [
            {"text": "最大", "provenance_kind": KIND_SOURCE, "source_locator": "p:2"},
            {"text": "最大值", "provenance_kind": KIND_SOURCE, "source_locator": "p:2"},
        ],
    }])
    prepared = cli.load_proposal_file(file, lexicon_name="cm-cli")
    assert [item.display_order for item in prepared[0]["proposals"]] == [1, 2]


def test_describe_entry_reports_what_is_shown_and_what_was_decided(admin) -> None:
    with admin.session() as session:
        lexicon = _system_lexicon(session, "cm-describe", "cm-test-describe")
        entry = _entry(session, lexicon, "seemingly", source_meanings=["看来；似乎"],
                       source_raw="seemingly adv. 看来；似乎")
        administrator = _user(session, admin)
        row = propose(
            session, entry=entry,
            proposals=[_proposal("看似", kind=KIND_AI_SUPPLEMENT, locator="",
                                 note="现有来源只有较少见义项，补充常见义")],
            actor=administrator,
        )[0]
        confirm(session, meaning=row, confirmer=administrator)
        session.commit()

        report = describe_entry(session, entry)

    assert [item["text"] for item in report["displayed"]] == ["看似"]
    assert report["displayed"][0]["is_supplement"] is True
    assert report["displayed"][0]["source_locator"] == ""
    assert report["source_default_meanings"] == ["看来；似乎"]
    assert report["source_raw"] == "seemingly adv. 看来；似乎"
    assert [item["action"] for item in report["history"]] == ["proposed", "confirmed"]


# --- the migration stack -----------------------------------------------------


def test_0009_follows_the_unpublished_0008_and_keeps_one_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0009_entry_concise_meaning"]
    revision = script.get_revision("0009_entry_concise_meaning")
    assert revision.down_revision == "0008_public_lexicon_import", (
        "0009 stacks on the unpublished 0008 rather than editing it in place"
    )
    # 0008 is untouched by this slice: it is still the revision the preflight branch
    # describes, and 0009 is the only thing that depends on it.
    assert script.get_revision("0008_public_lexicon_import").down_revision == (
        "0007_bridge_foreign_keys"
    )


def test_0009_is_additive_and_its_downgrade_is_exact(tmp_path: Path) -> None:
    """Round-trip on a disposable clone: two tables appear, nothing else moves."""
    import sqlite3

    from tests.conftest import run_alembic

    staging = tmp_path / "app-data" / "staging"
    staging.mkdir(parents=True)
    database = staging / "clone.db"
    elsewhere = tmp_path / "app-data" / "declared-real"
    elsewhere.mkdir()
    env = {"VOCAB_REAL_DATA_DIR": str(elsewhere)}

    assert run_alembic(database, "upgrade", "0008_public_lexicon_import",
                       extra_env=env).returncode == 0
    connection = sqlite3.connect(str(database))
    try:
        connection.execute(
            "insert into lexicon (owner_user_id, name, description, visibility, "
            "source_type, entry_count, created_at, updated_at) values (null, 'keep', "
            "'', 'public', 'keepme', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        connection.execute(
            "insert into lexicon_entry (lexicon_id, word, normalized_word, phonetic, "
            "part_of_speech, source_meanings, source_raw, default_anchor, semantic_note, "
            "possible_issue, created_at, updated_at) values (1, 'keepme', 'keepme', '', "
            "'', '[\"原义\"]', 'keepme n. 原义', '', '', 0, '2026-01-01 00:00:00', "
            "'2026-01-01 00:00:00')"
        )
        connection.commit()
        before = connection.execute(
            "select word, source_meanings, source_raw from lexicon_entry"
        ).fetchall()
        tables_before = {
            row[0] for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()

    assert run_alembic(database, "upgrade", "0009_entry_concise_meaning",
                       extra_env=env).returncode == 0
    connection = sqlite3.connect(str(database))
    try:
        tables_after = {
            row[0] for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        assert tables_after - tables_before == {
            "entry_concise_meaning", "entry_concise_meaning_revision"
        }
        assert connection.execute(
            "select word, source_meanings, source_raw from lexicon_entry"
        ).fetchall() == before, "0009 must not rewrite existing content"
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert connection.execute("pragma foreign_key_check").fetchall() == []
    finally:
        connection.close()

    assert run_alembic(database, "downgrade", "0008_public_lexicon_import",
                       extra_env=env).returncode == 0
    connection = sqlite3.connect(str(database))
    try:
        tables_back = {
            row[0] for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        assert tables_back == tables_before
        assert connection.execute(
            "select word, source_meanings, source_raw from lexicon_entry"
        ).fetchall() == before
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
