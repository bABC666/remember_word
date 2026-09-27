"""Administrator confirmation of a locked public-lexicon plan.

Everything here runs against synthetic CSV, a temporary migrated database and the
session's own system lexicon. The properties under test are the ones that make the
write safe to attempt at all: a retry must not write twice, a word already in the
lexicon must not be overwritten, a changed source file must refuse rather than
adapt, an unauthorised caller must change nothing, and a failure part-way through
must leave the database exactly as it was.

Every test uses source files with its own words and meanings. Evidence is keyed by
file bytes plus locator rather than by test, so two tests sharing a fixture file
would be two imports of the *same* source -- the second correctly recording
nothing new. Distinct content keeps each test a genuine first import.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.test_public_lexicon_plan import provenance_block


def _normalized(token: str) -> str:
    """The word these tests import, as ``strip().casefold()`` will see it."""
    return f"word{token}".lower()


def _primary_text(token: str) -> str:
    return (
        "head,cn\n"
        f"Word{token},主词表释义-{token}\n"
        f"Bare{token},\n"
    )


def _supplement_text(token: str) -> str:
    return (
        "term,translation,ipa,pos\n"
        f" word{token} ,补充来源释义-{token},/ˈ{token}/,noun\n"
    )


def _select_primary(token: str) -> dict[str, Any]:
    return {
        "normalized_word": _normalized(token), "field": "meaning", "action": "select",
        "evidence": [{"source_id": "primary", "line": 2}], "note": "以主词表释义为默认",
    }


def _select_supplement(token: str) -> dict[str, Any]:
    return {
        "normalized_word": _normalized(token), "field": "meaning", "action": "select",
        "evidence": [{"source_id": "supplement", "line": 2}],
        "note": "以补充来源释义为默认",
    }


def _exclude_bare(token: str) -> dict[str, Any]:
    return {
        "normalized_word": f"bare{token}".lower(), "action": "exclude_word",
        "note": "全部来源缺释义",
    }


# --- fixtures and helpers ----------------------------------------------------


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def _manifest(root: Path) -> Path:
    path = root / "manifest.json"
    path.write_text(json.dumps({
        "required_fields": ["meaning"],
        "sources": [
            {"id": "primary", "role": "primary", "file": "primary.csv",
             "columns": {"word": "head", "meaning": "cn"},
             "provenance": provenance_block("primary")},
            {"id": "supplement", "role": "meaning", "file": "supplement.csv",
             "columns": {"word": "term", "meaning": "translation",
                         "phonetic": "ipa", "part_of_speech": "pos"},
             "provenance": provenance_block("supplement")},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _decisions(root: Path, entries: list[dict[str, Any]]) -> Path:
    path = root / "decisions.json"
    path.write_text(
        json.dumps({"format_version": 1, "decisions": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _build_plan(
    root: Path, *, token: str, target: str, decisions: list[dict[str, Any]],
    rewrite_sources: bool = True,
) -> dict[str, Any]:
    from app.services.public_lexicon_plan import build_plan

    if rewrite_sources:
        _write(root, "primary.csv", _primary_text(token))
        _write(root, "supplement.csv", _supplement_text(token))
    return build_plan(
        manifest_path=_manifest(root),
        source_root=root,
        decisions_path=_decisions(root, decisions),
        target_lexicon=target,
    )


def _standard_decisions(token: str) -> list[dict[str, Any]]:
    return [_select_primary(token), _exclude_bare(token)]


def _system_lexicon(session, name: str, source_type: str):
    """A system public lexicon.

    ``uq_lexicon_system`` allows one system lexicon per ``source_type``, so tests
    needing a second target use a second source type.
    """
    from app.models import Lexicon

    lexicon = Lexicon(
        owner_user_id=None,
        name=name,
        description="synthetic target used by the Phase 2.9 confirmation tests",
        visibility="public",
        source_type=source_type,
    )
    session.add(lexicon)
    session.commit()
    session.refresh(lexicon)
    return lexicon


def _counts(session) -> dict[str, int]:
    from app.models import (
        EntrySourceEvidence,
        LexiconEntry,
        PublicImportRun,
        PublicImportRunSource,
        ReviewEvent,
        SourceArtifact,
        UserWordState,
    )

    return {
        "lexicon_entry": session.query(LexiconEntry).count(),
        "source_artifact": session.query(SourceArtifact).count(),
        "import_run": session.query(PublicImportRun).count(),
        "run_source": session.query(PublicImportRunSource).count(),
        "evidence": session.query(EntrySourceEvidence).count(),
        "user_word_state": session.query(UserWordState).count(),
        "review_event": session.query(ReviewEvent).count(),
    }


def _evidence_rows(session, run_id: int) -> list[dict[str, Any]]:
    from app.models import EntrySourceEvidence

    rows = session.query(EntrySourceEvidence).filter_by(import_run_id=run_id).order_by(
        EntrySourceEvidence.id
    ).all()
    return [
        {
            "id": row.id, "evidence_sha256": row.evidence_sha256,
            "field_kind": row.field_kind, "row_locator": row.row_locator,
            "raw_text": row.raw_text, "decision": row.decision,
            "selected_for_default": bool(row.selected_for_default),
            "selection_order": row.selection_order,
            "lexicon_entry_id": row.lexicon_entry_id,
            "normalized_word": row.normalized_word,
            "confirmed_at": str(row.confirmed_at),
        }
        for row in rows
    ]


@pytest.fixture()
def admin(make_world):
    """An active administrator with their own session-bound account."""
    value = make_world("p29-admin", role="admin")
    yield value
    value.client.__exit__(None, None, None)


def _administrator(session, world):
    from app.models import User

    return session.get(User, world.user_id)


# --- the happy path ----------------------------------------------------------


def test_confirm_writes_public_content_only(admin, tmp_path: Path) -> None:
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="happy", target="p29-happy",
                       decisions=_standard_decisions("happy"))

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-happy", "p29-test-happy")
        before = _counts(session)
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        after = _counts(session)
        entry = session.query(LexiconEntry).filter_by(
            lexicon_id=lexicon.id, normalized_word=_normalized("happy")
        ).one()
        session.refresh(lexicon)
        entry_count_after = lexicon.entry_count

    assert result["status"] == "applied"
    assert result["entries_created"] == 1
    assert result["entries_matched"] == 0
    # Content only: no learning state and no review history is created.
    assert after["user_word_state"] == before["user_word_state"]
    assert after["review_event"] == before["review_event"]
    # The adjudicated snapshot is what was written, verbatim.
    assert entry.word == "Wordhappy"
    assert entry.source_meanings == ["主词表释义-happy"]
    assert entry.source_raw == "Wordhappy,主词表释义-happy"
    assert entry.sequence == 1
    # Never written by an import: an AI product and unverified frequency data.
    assert entry.default_anchor == ""
    assert entry.frequency_rank is None and entry.frequency_count is None
    # The cached counter is deliberately left alone (design section 4.2).
    assert entry_count_after == 0
    assert after["lexicon_entry"] == before["lexicon_entry"] + 1
    assert after["source_artifact"] == before["source_artifact"] + 2
    assert after["import_run"] == before["import_run"] + 1


def test_confirm_records_every_source_value_with_its_decision(
    admin, tmp_path: Path
) -> None:
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="evid", target="p29-evid",
                       decisions=_standard_decisions("evid"))

    with admin.session() as session:
        _system_lexicon(session, "p29-evid", "p29-test-evid")
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        rows = _evidence_rows(session, result["import_run_id"])

    meaning = {(row["row_locator"], row["raw_text"]): row
               for row in rows if row["field_kind"] == "meaning"}
    assert meaning[(2, "主词表释义-evid")]["decision"] == "selected"
    assert meaning[(2, "主词表释义-evid")]["selected_for_default"] is True
    assert meaning[(2, "主词表释义-evid")]["selection_order"] == 0
    # The losing source keeps its own value and is recorded as not selected; it is
    # never merged into the winner.
    assert meaning[(2, "补充来源释义-evid")]["decision"] == "not_selected"
    assert meaning[(2, "补充来源释义-evid")]["selected_for_default"] is False
    assert {row["field_kind"] for row in rows} == {
        "word", "meaning", "phonetic", "part_of_speech",
    }
    assert all(row["normalized_word"] == _normalized("evid") for row in rows)
    assert all("；" not in row["raw_text"] for row in rows)


# --- idempotency -------------------------------------------------------------


def test_confirming_the_same_plan_twice_writes_nothing_the_second_time(
    admin, tmp_path: Path
) -> None:
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="retry", target="p29-retry",
                       decisions=_standard_decisions("retry"))

    with admin.session() as session:
        _system_lexicon(session, "p29-retry", "p29-test-retry")
        administrator = _administrator(session, admin)
        first = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        after_first = _counts(session)
        # A retry must not depend on the sources still being readable: it reports
        # what the first confirmation did.
        _write(root, "primary.csv", "head,cn\nChanged,改过了\n")
        second = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        after_second = _counts(session)

    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    assert second["run_id"] == first["run_id"]
    assert after_second == after_first


def test_confirming_into_a_second_lexicon_records_its_own_evidence(
    admin, tmp_path: Path
) -> None:
    """The same file and the same decision, into a second lexicon: its own rows.

    The evidence key covers the file bytes, the mapping, the locator, the field and the
    value. It deliberately does not cover the entry, so "this source value is already on
    record" has to be asked of *the entry this run writes*: asked of the table at large,
    it is answered by the first lexicon's word, the second import records nothing, and
    the entry it created has no source record of its own at all.

    The artifact, by contrast, is the same input read the same way, so it *is* reused by
    fingerprint rather than re-created under a new label.
    """
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    decisions = _standard_decisions("reuse")
    first_plan = _build_plan(root, token="reuse", target="p29-reuse-a",
                             decisions=decisions)
    second_plan = _build_plan(root, token="reuse", target="p29-reuse-b",
                              decisions=decisions, rewrite_sources=False)

    with admin.session() as session:
        second_lexicon = _system_lexicon(session, "p29-reuse-b", "p29-test-reuse-b")
        _system_lexicon(session, "p29-reuse-a", "p29-test-reuse-a")
        administrator = _administrator(session, admin)
        first = confirm_plan(
            session, plan=first_plan, administrator=administrator, source_root=root
        )
        artifacts_after_first = _counts(session)["source_artifact"]
        rows_first = _evidence_rows(session, first["import_run_id"])
        second = confirm_plan(
            session, plan=second_plan, administrator=administrator, source_root=root
        )
        rows_second = _evidence_rows(session, second["import_run_id"])
        artifacts_after_second = _counts(session)["source_artifact"]
        second_entry = session.query(LexiconEntry).filter_by(
            lexicon_id=second_lexicon.id, normalized_word=_normalized("reuse")
        ).one()

    assert first["plan_sha256"] != second_plan["plan_sha256"]
    assert second["status"] == "applied"
    # The word is new to the second lexicon, so an entry is created ...
    assert second["entries_created"] == 1
    # ... and it gets the per-field evidence of its own import.
    assert rows_first, "control: the first lexicon's entry recorded its evidence"
    assert len(rows_second) == len(rows_first)
    assert {row["lexicon_entry_id"] for row in rows_second} == {second_entry.id}, (
        "every row of this run has to be a record of this lexicon's entry"
    )
    assert {row["id"] for row in rows_second} & {row["id"] for row in rows_first} == set()
    assert second["evidence"]["written"] == len(rows_second)
    assert second["evidence"]["skipped_existing"] == 0, (
        "nothing was on record *for this entry*, so nothing could be skipped"
    )
    # The artifact is reused by fingerprint, not re-created under a new label.
    assert artifacts_after_second == artifacts_after_first


# --- conflicts with content already in the lexicon ---------------------------


def test_a_word_already_in_the_lexicon_is_reported_and_not_overwritten(
    admin, tmp_path: Path
) -> None:
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="confl", target="p29-confl",
                       decisions=_standard_decisions("confl"))

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-confl", "p29-test-confl")
        existing = LexiconEntry(
            lexicon_id=lexicon.id, word="Wordconfl",
            normalized_word=_normalized("confl"),
            phonetic="/existing/", part_of_speech="noun",
            source_meanings=["既有释义，不得覆盖"], source_raw="既有原文", sequence=7,
        )
        session.add(existing)
        session.commit()
        session.refresh(existing)
        before = (
            existing.id, existing.word, existing.phonetic,
            list(existing.source_meanings), existing.source_raw, existing.sequence,
        )
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        session.refresh(existing)
        after = (
            existing.id, existing.word, existing.phonetic,
            list(existing.source_meanings), existing.source_raw, existing.sequence,
        )
        rows = _evidence_rows(session, result["import_run_id"])

    assert result["entries_created"] == 0
    assert result["entries_matched"] == 1
    assert result["conflicts"] == [{
        "normalized_word": _normalized("confl"),
        "reason": "already_in_lexicon",
        "lexicon_entry_id": existing.id,
    }]
    assert after == before
    # Nothing was adopted for the matched word, so no evidence claims otherwise.
    assert rows == []


# --- the sources must still be the ones the plan froze -----------------------


def test_a_changed_source_file_refuses_the_plan(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="chang", target="p29-chang",
                       decisions=_standard_decisions("chang"))

    with admin.session() as session:
        _system_lexicon(session, "p29-chang", "p29-test-chang")
        before = _counts(session)
        _write(root, "primary.csv", "head,cn\nWordchang,换了一个释义\nBarechang,\n")
        with pytest.raises(ConfirmRefused, match="文件内容已变化"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert after == before


def test_a_deleted_source_file_refuses_the_plan(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="miss", target="p29-miss",
                       decisions=_standard_decisions("miss"))

    with admin.session() as session:
        _system_lexicon(session, "p29-miss", "p29-test-miss")
        before = _counts(session)
        (root / "supplement.csv").unlink()
        with pytest.raises(ConfirmRefused, match="无法重新读取"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert after == before


def _revision_manifest(root: Path) -> Path:
    """The standard two sources, with the primary one declaring a per-row revision."""
    path = root / "manifest.json"
    path.write_text(json.dumps({
        "required_fields": ["meaning"],
        "sources": [
            {"id": "primary", "role": "primary", "file": "primary.csv",
             "columns": {"word": "head", "meaning": "cn"},
             "revision": {
                 "column": "oldid",
                 "url_template": "https://zh.wiktionary.org/w/index.php?oldid={revision}",
             },
             "provenance": provenance_block("primary")},
            {"id": "supplement", "role": "meaning", "file": "supplement.csv",
             "columns": {"word": "term", "meaning": "translation",
                         "phonetic": "ipa", "part_of_speech": "pos"},
             "provenance": provenance_block("supplement")},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return path


# --- the pinned revision a value was read at ---------------------------------
#
# The plan freezes the revision of every evidence row it cites (design 3.4, the plan
# step). Confirmation *re-derives* each one from the source file and locator and only
# then writes it, for the same reason it re-derives every value: the plan's digest
# covers the claim, but a digest is not a signature and the claim has to be re-proved
# against the bytes before anything is stored.

PINNED_TEMPLATE = "https://zh.wiktionary.org/w/index.php?oldid={revision}"


def _revision_primary_text(token: str, revision: str = "6588944") -> str:
    """The standard primary file plus one revision column per row."""
    return (
        "head,cn,oldid\n"
        f"Word{token},主词表释义-{token},{revision}\n"
        f"Bare{token},,\n"
    )


def _revision_supplement_text(token: str, revision: str = "70dc6b68") -> str:
    """The supplement file too, so one word has two sources at two revisions."""
    return (
        "term,translation,ipa,pos,rev\n"
        f" word{token} ,补充来源释义-{token},/ˈ{token}/,noun,{revision}\n"
    )


def _revision_manifest(root: Path) -> Path:
    """The standard two sources, each declaring where its rows' revisions come from.

    Both sources declare a *column*, and they declare different ones, so "each row
    carries the revision of the row it was read from" has a wrong answer available:
    taking the primary's number for the supplement's row would be invisible if only one
    source ever carried a revision.
    """
    path = root / "manifest.json"
    path.write_text(json.dumps({
        "required_fields": ["meaning"],
        "sources": [
            {"id": "primary", "role": "primary", "file": "primary.csv",
             "columns": {"word": "head", "meaning": "cn"},
             "revision": {"column": "oldid", "url_template": PINNED_TEMPLATE},
             "provenance": provenance_block("primary")},
            {"id": "supplement", "role": "meaning", "file": "supplement.csv",
             "columns": {"word": "term", "meaning": "translation",
                         "phonetic": "ipa", "part_of_speech": "pos"},
             "revision": {"column": "rev", "url_template": PINNED_TEMPLATE},
             "provenance": provenance_block("supplement")},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _build_revision_plan(
    root: Path, *, token: str, target: str, decisions: list[dict[str, Any]],
    primary_revision: str = "6588944", supplement_revision: str = "70dc6b68",
) -> dict[str, Any]:
    from app.services.public_lexicon_plan import build_plan

    _write(root, "primary.csv", _revision_primary_text(token, primary_revision))
    _write(root, "supplement.csv", _supplement_text(token))
    _write(root, "supplement.csv",
           _revision_supplement_text(token, supplement_revision))
    return build_plan(
        manifest_path=_revision_manifest(root),
        source_root=root,
        decisions_path=_decisions(root, decisions),
        target_lexicon=target,
    )


def _retarget_plan(plan: dict[str, Any], name: str) -> dict[str, Any]:
    """The same plan, aimed at a differently named target, with its digest recomputed.

    A plan's digest covers the target's *name*, and two system public lexicons may not
    share one (``_target_lexicon`` refuses the ambiguity), so a second confirmation of
    the same source into a second lexicon needs a second plan. Everything that decides
    the *evidence* -- bytes, mapping, adjudication, revisions -- is untouched, so the
    evidence keys stay identical; only the destination and the digest differ. This is
    the sanctioned use of a recomputed digest: the operator is not the adversary here,
    and every value the path writes is still re-derived from the sources.
    """
    from app.services.public_lexicon_plan import plan_digest

    retargeted = json.loads(json.dumps(plan))
    retargeted["target"]["lexicon"] = name
    retargeted["plan_sha256"] = plan_digest(retargeted)
    return retargeted


def _revision_rows(session, run_id: int) -> list[dict[str, Any]]:
    """Every evidence row of one run, with the revision it recorded."""
    from app.models import EntrySourceEvidence

    return [
        {
            "evidence_sha256": row.evidence_sha256,
            "field_kind": row.field_kind,
            "normalized_word": row.normalized_word,
            "source_revision": row.source_revision,
        }
        for row in session.query(EntrySourceEvidence).filter_by(
            import_run_id=run_id
        ).order_by(EntrySourceEvidence.id).all()
    ]


def test_a_revision_declaring_plan_confirms_and_records_each_rows_revision(
    admin, tmp_path: Path
) -> None:
    """The declaration is passed back, so the plan confirms and the revision is stored.

    The plan step added the declaration to the frozen mapping; the confirmation path
    rebuilds the mapping from the plan's own fields, so it has to read the declaration
    back or the digest it recomputes stops matching and every revision-declaring plan
    refuses. Reading it back is only half the job: what lands on the evidence row is
    re-derived per row, so the same word present in two sources at two revisions keeps
    them apart instead of sharing whichever number was read first.
    """
    from app.models import SourceArtifact
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    token = "revok"
    plan = _build_revision_plan(root, token=token, target="p29-revok",
                                decisions=_standard_decisions(token))
    word = _normalized(token)
    entry = next(item for item in plan["entries"] if item["normalized_word"] == word)
    assert [item["source_revision"] for item in entry["evidence"]["word"]] == [
        "6588944", "70dc6b68",
    ], "control: the plan froze two different revisions for one word"
    assert plan["confirmation_ready"] is True, (
        "a readiness blocker would refuse the plan for a different reason"
    )

    with admin.session() as session:
        _system_lexicon(session, "p29-revok", "p29-test-revok")
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        rows = _revision_rows(session, result["import_run_id"])
        roles = {
            artifact.id: artifact.role
            for artifact in session.query(SourceArtifact).all()
        }

    assert result["status"] == "applied"
    assert rows != []
    # Per row, not per source and not per file: the primary's rows and the
    # supplement's all carry the revision of the row each one cites.
    assert {row["source_revision"] for row in rows} == {"6588944", "70dc6b68"}
    assert all(row["source_revision"] for row in rows)
    # The word exists in both sources, and each of its rows keeps its own number.
    assert {
        row["source_revision"] for row in rows if row["field_kind"] == "word"
    } == {"6588944", "70dc6b68"}
    # Only the adjudicated word is recorded: the excluded one has no evidence at all.
    assert {row["normalized_word"] for row in rows} == {word}
    assert set(roles.values()) == {"primary", "meaning"}


def test_a_tampered_revision_with_a_recomputed_digest_is_refused(
    admin, tmp_path: Path
) -> None:
    """The revision is re-derived from the file, so editing it cannot get through.

    ``plan_sha256`` catches an accidental edit and is explicitly not a signature: this
    rewrites one frozen revision, recomputes the digest the way an editor would, and
    expects confirmation to refuse. The file still holds 6588944, so the plan's claim
    is the thing that is wrong -- which is exactly what a digest cannot detect on its
    own.
    """
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    token = "revtmpr"
    plan = _build_revision_plan(root, token=token, target="p29-revtmpr",
                                decisions=_standard_decisions(token))
    word = _normalized(token)
    entry = next(item for item in plan["entries"] if item["normalized_word"] == word)
    entry["evidence"]["word"][0]["source_revision"] = "9999999"
    plan["plan_sha256"] = plan_digest(plan)
    assert plan["plan_sha256"] == plan["plan_sha256"], "control: the digest is consistent"

    with admin.session() as session:
        _system_lexicon(session, "p29-revtmpr", "p29-test-revtmpr")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="修订"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert after == before, "a refused plan must write nothing at all"


def test_a_changed_revision_cell_with_an_unchanged_value_refuses_the_plan(
    admin, tmp_path: Path
) -> None:
    """Changing only a revision cell changes the file, so the old plan no longer fits.

    The gate that catches this is the file fingerprint, and this test states why that
    is the right refusal rather than a re-derivation: the plan describes a read of
    specific bytes, and a source whose pinned revision moved is a different read even
    when every imported value is byte-identical. The word, its meaning and every other
    value are deliberately unchanged, so nothing but the revision differs.
    """
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    token = "revmove"
    plan = _build_revision_plan(root, token=token, target="p29-revmove",
                                decisions=_standard_decisions(token))
    _write(root, "primary.csv", _revision_primary_text(token, "7500000"))

    with admin.session() as session:
        _system_lexicon(session, "p29-revmove", "p29-test-revmove")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="文件内容已变化"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert after == before, "a refused plan must write nothing at all"


def test_an_empty_revision_cell_is_confirmed_and_stored_as_empty(
    admin, tmp_path: Path
) -> None:
    """A word with no pinned revision upstream records the honest blank.

    "This row has no revision" is what the empty cell means, and it must survive to the
    evidence row as an empty value rather than as a guessed number or a refusal: the
    meaning is real evidence, and the absence of a link is a fact a reader degrades on.
    """
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    token = "revempt"
    plan = _build_revision_plan(root, token=token, target="p29-revempt",
                                decisions=_standard_decisions(token),
                                primary_revision="")
    word = _normalized(token)
    entry = next(item for item in plan["entries"] if item["normalized_word"] == word)
    assert entry["evidence"]["word"][0]["source_revision"] == "", (
        "control: the plan kept the empty cell rather than inventing a revision"
    )

    with admin.session() as session:
        _system_lexicon(session, "p29-revempt", "p29-test-revempt")
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        rows = _revision_rows(session, result["import_run_id"])

    assert result["status"] == "applied"
    primary_rows = [row for row in rows if row["source_revision"] in ("", "70dc6b68")]
    assert len(primary_rows) == len(rows), "every row carries one of the two declarations"
    assert any(row["source_revision"] == "" for row in rows)
    assert any(row["source_revision"] == "70dc6b68" for row in rows)


def test_a_different_source_keeps_its_own_revision_for_the_same_word(
    admin, tmp_path: Path
) -> None:
    """Two sources, one word, two revisions: neither row borrows the other's number."""
    from app.models import EntrySourceEvidence
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    token = "revtwo"
    plan = _build_revision_plan(root, token=token, target="p29-revtwo",
                                decisions=_standard_decisions(token),
                                primary_revision="1111111",
                                supplement_revision="2222222")

    with admin.session() as session:
        _system_lexicon(session, "p29-revtwo", "p29-test-revtwo")
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        rows = session.query(EntrySourceEvidence).filter_by(
            import_run_id=result["import_run_id"]
        ).all()
        revisions = {row.source_revision for row in rows}

    assert result["status"] == "applied"
    assert revisions == {"1111111", "2222222"}, (
        f"each evidence row must carry its own source row's revision, got {revisions}"
    )


def test_a_repeated_confirmation_of_a_revision_declaring_plan_writes_nothing(
    admin, tmp_path: Path
) -> None:
    """A retry is answered from the recorded run, with or without a revision."""
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    token = "revretry"
    plan = _build_revision_plan(root, token=token, target="p29-revretry",
                                decisions=_standard_decisions(token))

    with admin.session() as session:
        _system_lexicon(session, "p29-revretry", "p29-test-revretry")
        administrator = _administrator(session, admin)
        first = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        after_first = _counts(session)
        rows_after_first = _revision_rows(session, first["import_run_id"])
        # Even with the sources moved, the retry reports what the first run did.
        _write(root, "supplement.csv", _revision_supplement_text(token, "3333333"))
        second = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        after_second = _counts(session)

    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    assert second["run_id"] == first["run_id"]
    assert after_second == after_first
    # The first run's rows are exactly what they were, revisions included.
    assert _revision_rows(session, first["import_run_id"]) == rows_after_first


def test_a_retry_of_the_same_plan_records_no_evidence_again(
    admin, tmp_path: Path
) -> None:
    """Scoping the deduplication to the entry must not turn a retry into an append.

    "A new entry always gets its own rows" is the rule; "every call appends its rows
    again" is not. The same plan confirmed twice is answered from the recorded run, and
    the target entry keeps exactly the rows the first call wrote -- the same ids, the
    same count, the same revisions -- with the table counts unchanged.
    """
    from app.models import EntrySourceEvidence, LexiconEntry
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    token = "retryzero"
    plan = _build_revision_plan(root, token=token, target="p29-retryzero",
                                decisions=_standard_decisions(token))

    def evidence_ids(session, lexicon_id: int) -> list[int]:
        entry_ids = [
            row[0] for row in session.query(LexiconEntry.id).filter_by(
                lexicon_id=lexicon_id
            ).all()
        ]
        return sorted(
            row[0] for row in session.query(EntrySourceEvidence.id).filter(
                EntrySourceEvidence.lexicon_entry_id.in_(entry_ids)
            ).all()
        )

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-retryzero", "p29-test-retryzero")
        administrator = _administrator(session, admin)
        first = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        before = evidence_ids(session, lexicon.id)
        counts_before = _counts(session)
        second = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        after = evidence_ids(session, lexicon.id)
        counts_after = _counts(session)

    assert first["status"] == "applied" and before, "control: the first call recorded"
    assert second["status"] == "already_applied"
    assert second["import_run_id"] == first["import_run_id"]
    assert after == before, "a retry writes no evidence row at all"
    assert counts_after == counts_before
    assert second["evidence_written"] == first["evidence_written"] == len(before)


def test_a_stale_revision_under_the_same_key_is_neither_reused_nor_rewritten(
    admin, tmp_path: Path
) -> None:
    """A record under the same evidence key, but not of this entry, decides nothing.

    Evidence is deduplicated by key, and the key deliberately does not carry the entry
    (or the revision -- a revision is metadata about the row, not part of the value's
    identity). So the state this has to answer for is a row that shares the key with a
    value this run would record and yet is not a record of the entry being written: what
    a writer predating the revision column leaves behind, and what a deleted entry
    leaves behind, since ``lexicon_entry_id`` is ``ON DELETE SET NULL``.

    Such a row must neither be treated as "already recorded for this entry" nor be
    rewritten with the revision this run read. The entry gets a row of its own carrying
    that revision, and the stale row stays exactly as it was.
    """
    from app.models import EntrySourceEvidence, LexiconEntry, PublicImportRunSource, SourceArtifact
    from app.services.public_lexicon_confirm import confirm_plan
    from app.services.public_lexicon_plan import evidence_idempotency_key

    root = tmp_path / "sources"
    root.mkdir()
    token = "revskip"
    plan = _build_revision_plan(root, token=token, target="p29-revskip-a",
                                decisions=_standard_decisions(token))
    source = next(item for item in plan["sources"] if item["source_id"] == "primary")
    word = _normalized(token)
    meaning = next(
        item for item in next(
            entry for entry in plan["entries"] if entry["normalized_word"] == word
        )["evidence"]["meaning"]
        if item["source_id"] == "primary"
    )
    key = evidence_idempotency_key(
        file_sha256=source["file"]["sha256"],
        mapping_sha256=source["mapping_sha256"],
        line=meaning["line"], field="meaning", raw_value=meaning["raw_value"],
    )
    assert key == meaning["idempotency_key"], "control: the key is the plan's own"

    with admin.session() as session:
        first_lexicon = _system_lexicon(session, "p29-revskip-a", "p29-test-revskip-a")
        second_lexicon = _system_lexicon(session, "p29-revskip-b", "p29-test-revskip-b")
        administrator = _administrator(session, admin)
        first = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        # Stand the first run down so the row below is the *only* record of this key.
        for row in session.query(EntrySourceEvidence).filter_by(
            import_run_id=first["import_run_id"], evidence_sha256=key
        ).all():
            session.delete(row)
        session.commit()
        # The primary source's artifact, as *this* run recorded it -- the artifact table
        # is shared across the session's tests, so it is reached through the run.
        artifact_id = session.query(PublicImportRunSource.source_artifact_id).join(
            SourceArtifact,
            SourceArtifact.id == PublicImportRunSource.source_artifact_id,
        ).filter(
            PublicImportRunSource.import_run_id == first["import_run_id"],
            SourceArtifact.role == "primary",
        ).one()[0]
        session.add(EntrySourceEvidence(
            lexicon_entry_id=None, source_artifact_id=artifact_id,
            import_run_id=first["import_run_id"], normalized_word=word,
            row_locator=meaning["line"], field_kind="meaning",
            sense_key=f"meaning@{meaning['line']}", raw_word=word,
            raw_text=meaning["raw_value"], evidence_sha256=key,
            decision="not_selected", selected_for_default=False, selection_order=None,
            confirmed_by_username="earlier-read", confirmed_at=datetime.now(UTC),
            source_revision="0000000",
        ))
        session.commit()

        # The same evidence, aimed at a second lexicon: a real second write, under the
        # same evidence key, because the first lexicon already holds the word.
        second = confirm_plan(
            session, plan=_retarget_plan(plan, "p29-revskip-b"),
            administrator=administrator, source_root=root,
        )
        recorded = session.query(EntrySourceEvidence).filter_by(
            evidence_sha256=key
        ).order_by(EntrySourceEvidence.id).all()
        revisions = [row.source_revision for row in recorded]
        decisions = [row.decision for row in recorded]
        runs = {first["import_run_id"], second["import_run_id"]}
        second_entry = session.query(LexiconEntry).filter_by(
            lexicon_id=second_lexicon.id, normalized_word=word
        ).one()
        second_rows = _evidence_rows(session, second["import_run_id"])

    assert first["target_lexicon"]["id"] == first_lexicon.id
    assert second["target_lexicon"]["id"] == second_lexicon.id
    assert runs == {first["import_run_id"], second["import_run_id"]}
    assert first["status"] == "applied" and second["status"] == "applied"
    # The second run wrote every item it considered, for its own entry: the stale row
    # belongs to no entry of this lexicon, so it decides nothing about this one.
    assert second["evidence"]["written"] == len(second_rows) == 6
    assert second["evidence"]["skipped_existing"] == 0
    assert second["evidence"]["readjudicated"] == 0
    assert {row["lexicon_entry_id"] for row in second_rows} == {second_entry.id}
    # The stale row is still there, unrewritten, beside the row this run recorded: the
    # revision it read reaches a row of its own rather than being silently dropped.
    assert revisions == ["0000000", "6588944"], (
        f"the run's own revision has to reach a row of its own, got {revisions}"
    )
    assert decisions == ["not_selected", "selected"]


# --- what must never be confirmable ------------------------------------------


def test_an_unadjudicated_plan_is_refused(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="block", target="p29-block",
                       decisions=[_exclude_bare("block")])

    with admin.session() as session:
        _system_lexicon(session, "p29-block", "p29-test-block")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="尚未就绪"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert plan["confirmation_ready"] is False
    assert after == before


def test_a_tampered_plan_is_refused(admin, tmp_path: Path) -> None:
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="tampr", target="p29-tampr",
                       decisions=_standard_decisions("tampr"))
    plan["entries"][0]["default_snapshot"]["source_meanings"] = ["被改过的释义"]

    with admin.session() as session:
        _system_lexicon(session, "p29-tampr", "p29-test-tampr")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="摘要与内容不符"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert after == before


def test_a_recomputed_digest_cannot_smuggle_a_default_no_source_states(
    admin, tmp_path: Path
) -> None:
    """A hand-edited plan that recomputes its own digest must still be refused.

    ``plan_sha256`` catches an accidental edit; it is not a signature, and anyone who
    edits the plan can recompute it. That is exactly the actor the provenance gate
    already defends against, so the value the plan would *write* has to be re-derived
    from the evidence the plan itself points at. Without that, a plan could store one
    meaning on ``lexicon_entry`` while ``entry_source_evidence`` recorded a different
    -- and true -- source value as ``selected``, leaving an audit trail that
    contradicts the content it claims to justify.
    """
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="smugl", target="p29-smugl",
                       decisions=_standard_decisions("smugl"))
    ready = next(entry for entry in plan["entries"] if entry["status"] == "ready")
    ready["default_snapshot"]["source_meanings"] = ["完全编造的释义"]
    plan["plan_sha256"] = plan_digest(plan)

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-smugl", "p29-test-smugl")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="证据"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)
        written = session.query(LexiconEntry).filter_by(lexicon_id=lexicon.id).all()

    assert after == before
    assert written == []


def test_a_recomputed_digest_cannot_point_a_default_at_another_source_value(
    admin, tmp_path: Path
) -> None:
    """The locator the plan declares as selected is checked too, not just the text."""
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="devia", target="p29-devia",
                       decisions=_standard_decisions("devia"))
    ready = next(entry for entry in plan["entries"] if entry["status"] == "ready")
    # Keep the written text plausible but claim a *different* row selected it: the
    # entry would then carry a value whose evidence row says something else.
    ready["default_snapshot"]["source_meanings"] = ["主词表释义-devia"]
    ready["default_evidence"]["meaning"] = [{"source_id": "supplement", "line": 2}]
    plan["plan_sha256"] = plan_digest(plan)

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-devia", "p29-test-devia")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="证据"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)
        written = session.query(LexiconEntry).filter_by(lexicon_id=lexicon.id).all()

    assert after == before
    assert written == []


def test_a_recomputed_digest_cannot_rewrite_the_primary_raw_line(
    admin, tmp_path: Path
) -> None:
    """``source_raw`` is the anchor the whole slice promises can be re-checked."""
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="rawln", target="p29-rawln",
                       decisions=_standard_decisions("rawln"))
    ready = next(entry for entry in plan["entries"] if entry["status"] == "ready")
    ready["default_snapshot"]["source_raw"] = "Wordrawln,伪造的整行"
    plan["plan_sha256"] = plan_digest(plan)

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-rawln", "p29-test-rawln")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="source_raw"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)
        written = session.query(LexiconEntry).filter_by(lexicon_id=lexicon.id).all()

    assert after == before
    assert written == []


def test_the_written_entry_identity_is_re_derived_from_the_written_word(
    admin, tmp_path: Path
) -> None:
    """An edited ``normalized_word`` must be refused, not written as a new word.

    ``normalized_word`` is not displayed, but it is written -- onto ``lexicon_entry``
    and onto every evidence row -- and it is the value that decides *which* word the
    import creates. Nothing re-derived it, so a hand-edited plan that recomputed its
    own digest could store content that is honestly source-derived under an identity
    no source declares: the entry count rose, the evidence rows agreed with the forged
    identity, and no report said anything was wrong.
    """
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="ident", target="p29-ident",
                       decisions=_standard_decisions("ident"))
    ready = next(entry for entry in plan["entries"] if entry["status"] == "ready")
    assert ready["normalized_word"] == _normalized("ident"), "control: the honest identity"
    ready["normalized_word"] = "身份并不来自任何来源"
    plan["plan_sha256"] = plan_digest(plan)

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-ident", "p29-test-ident")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="词身份"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)
        entries = session.query(LexiconEntry).filter_by(lexicon_id=lexicon.id).all()

    assert after == before, "a refused plan writes nothing at all"
    assert after["evidence"] == before["evidence"], (
        "and no evidence row may record that identity either"
    )
    assert entries == [], "no entry may carry an identity its own source never stated"


def test_an_identity_aimed_at_a_word_the_lexicon_already_holds_is_refused(
    admin, tmp_path: Path
) -> None:
    """The silent half of the same hole: an edited identity became an "ordinary conflict".

    Pointing a ready word's identity at a spelling the target lexicon already holds
    made the run report ``already_in_lexicon`` and write **nothing** -- ``applied``,
    no error, and the word the operator meant to import simply absent from the public
    lexicon. The refusal has to happen on the plan, not be reported as a conflict.
    """
    from app.models import EntrySourceEvidence, LexiconEntry
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="collid", target="p29-collid",
                       decisions=_standard_decisions("collid"))
    ready = next(entry for entry in plan["entries"] if entry["status"] == "ready")
    ready["normalized_word"] = "occupied"
    plan["plan_sha256"] = plan_digest(plan)

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-collid", "p29-test-collid")
        held = LexiconEntry(
            lexicon_id=lexicon.id, word="Occupied", normalized_word="occupied",
            phonetic="", part_of_speech="", source_meanings=["既有释义"],
            source_raw="Occupied,既有释义", sequence=1,
        )
        session.add(held)
        session.commit()
        before = _counts(session)
        before_entry = (
            held.id, held.word, held.normalized_word, list(held.source_meanings),
            held.source_raw,
        )
        with pytest.raises(ConfirmRefused, match="词身份"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        session.refresh(held)
        after = _counts(session)
        after_entry = (
            held.id, held.word, held.normalized_word, list(held.source_meanings),
            held.source_raw,
        )
        # Scoped to the entry this plan would have written into: the session database is
        # shared across this module, so other tests' evidence rows are beside it.
        evidence = session.query(EntrySourceEvidence).filter_by(
            lexicon_entry_id=held.id
        ).all()
        entries = session.query(LexiconEntry).filter_by(lexicon_id=lexicon.id).count()

    assert after == before, "a refused plan writes nothing at all"
    assert after_entry == before_entry, "the word already held must stay exactly as it was"
    assert entries == 1, "only the pre-existing entry exists"
    assert evidence == [], "no evidence may be recorded against a refused plan's target"


def test_a_word_override_that_changes_the_spelling_still_confirms(
    admin, tmp_path: Path
) -> None:
    """The legitimate case the derivation must not break.

    A human may select the *word* field from a supplementary source, whose spelling of
    the same word differs from the primary's. Both spellings normalize to the same
    identity, so the identity is still exactly what the written word produces and the
    plan confirms.
    """
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    token = "spell"
    decisions = [
        _select_primary(token),
        _exclude_bare(token),
        {
            "normalized_word": _normalized(token), "field": "word", "action": "select",
            "evidence": [{"source_id": "supplement", "line": 2}],
            "note": "人工改选补充来源的显示拼写",
        },
    ]
    plan = _build_plan(root, token=token, target="p29-spell", decisions=decisions)
    ready = next(entry for entry in plan["entries"] if entry["status"] == "ready")
    assert plan["confirmation_ready"] is True, plan["confirmation_blockers"]
    # The point of the case: the display spelling is not the primary's, yet the
    # identity is unchanged.
    assert ready["default_snapshot"]["word"].strip() != f"Word{token}", (
        "control: the override really did change the spelling"
    )

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-spell", "p29-test-spell")
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        entry = session.query(LexiconEntry).filter_by(
            lexicon_id=lexicon.id, normalized_word=_normalized(token)
        ).one()
        written_word = entry.word

    assert result["status"] == "applied"
    assert result["entries_created"] == 1
    assert written_word == ready["default_snapshot"]["word"]
    assert written_word.strip().casefold() == _normalized(token)


def test_a_malformed_snapshot_is_refused_rather_than_crashing(
    admin, tmp_path: Path
) -> None:
    """A digest-recomputed plan with a null snapshot refuses, and does not raise TypeError."""
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="nulls", target="p29-nulls",
                       decisions=_standard_decisions("nulls"))
    ready = next(entry for entry in plan["entries"] if entry["status"] == "ready")
    ready["default_snapshot"] = None
    plan["plan_sha256"] = plan_digest(plan)

    with admin.session() as session:
        _system_lexicon(session, "p29-nulls", "p29-test-nulls")
        before = _counts(session)
        with pytest.raises(ConfirmRefused):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert after == before


def test_a_run_recorded_against_another_lexicon_is_not_a_retry(admin, tmp_path: Path) -> None:
    """``already_applied`` must mean "applied *here*", or it tells the operator a lie.

    ``plan_sha256`` freezes the target lexicon's name, not its id. Rename the lexicon a
    plan was applied to and give its name to a new one, and the digest still resolves --
    to the new lexicon. Answering the retry shortcut then would report a completed
    import while writing nothing to the target the administrator is looking at.
    """
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="retgt", target="p29-retgt",
                       decisions=_standard_decisions("retgt"))

    with admin.session() as session:
        first = _system_lexicon(session, "p29-retgt", "p29-test-retgt-a")
        administrator = _administrator(session, admin)
        original = confirm_plan(
            session, plan=plan, administrator=administrator, source_root=root
        )
        first.name = "p29-retgt-renamed"
        session.commit()
        second = _system_lexicon(session, "p29-retgt", "p29-test-retgt-b")
        before = _counts(session)

        with pytest.raises(ConfirmRefused, match="另一个公共词库"):
            confirm_plan(
                session, plan=plan, administrator=administrator, source_root=root
            )
        after = _counts(session)
        in_second = session.query(LexiconEntry).filter_by(lexicon_id=second.id).count()

    assert original["status"] == "applied"
    assert original["target_lexicon"]["id"] == first.id
    assert after == before
    assert in_second == 0


def test_a_missing_or_ambiguous_target_lexicon_is_refused(
    admin, tmp_path: Path
) -> None:
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="ambig", target="p29-ambig",
                       decisions=_standard_decisions("ambig"))

    with admin.session() as session:
        administrator = _administrator(session, admin)
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="找不到名为"):
            confirm_plan(
                session, plan=plan, administrator=administrator, source_root=root
            )
        # Two system lexicons with one name: guessing one would silently target the
        # wrong list, so this refuses instead.
        _system_lexicon(session, "p29-ambig", "p29-test-ambig-a")
        _system_lexicon(session, "p29-ambig", "p29-test-ambig-b")
        with pytest.raises(ConfirmRefused, match="无法确定目标"):
            confirm_plan(
                session, plan=plan, administrator=administrator, source_root=root
            )
        after = _counts(session)

    assert after == before


# --- permissions -------------------------------------------------------------


@pytest.mark.parametrize(("role", "is_active", "message"), [
    ("user", True, "不是管理员"),
    ("admin", False, "已停用"),
])
def test_a_caller_without_the_capability_changes_nothing(
    admin, make_world, tmp_path: Path, role: str, is_active: bool, message: str
) -> None:
    from app.models import User
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan

    token = f"perm{role[:1]}{int(is_active)}"
    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token=token, target=f"p29-{token}",
                       decisions=_standard_decisions(token))
    other = make_world(f"p29-other-{token}", role=role)

    try:
        with admin.session() as session:
            _system_lexicon(session, f"p29-{token}", f"p29-test-{token}")
            caller = session.get(User, other.user_id)
            caller.is_active = is_active
            session.commit()
            before = _counts(session)
            with pytest.raises(ConfirmRefused, match=message):
                confirm_plan(
                    session, plan=plan, administrator=caller, source_root=root
                )
            after = _counts(session)
    finally:
        other.client.__exit__(None, None, None)

    assert after == before


# --- atomicity ---------------------------------------------------------------


def test_a_failure_inside_the_write_rolls_everything_back(
    admin, tmp_path: Path, monkeypatch
) -> None:
    from app.models import LexiconEntry
    from app.services import public_lexicon_confirm as service

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="rollb", target="p29-rollb",
                       decisions=_standard_decisions("rollb"))

    def explode(*_args, **_kwargs):
        # Raised only after the artifacts and the new entries exist, so the assertions
        # below are about the rollback and not about ordering.
        raise RuntimeError("injected failure while recording evidence")

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-rollb", "p29-test-rollb")
        before = _counts(session)
        monkeypatch.setattr(service, "_plan_evidence", explode)
        with pytest.raises(RuntimeError, match="injected failure"):
            service.confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)
        orphans = session.query(LexiconEntry).filter_by(lexicon_id=lexicon.id).count()

    assert after == before
    assert orphans == 0


# --- immutable history -------------------------------------------------------


def test_two_lexicons_keep_two_histories_and_neither_is_rewritten(
    admin, tmp_path: Path
) -> None:
    """The same source value decided differently for two lexicons: two entries, no edit.

    One source position, adjudicated one way for the first lexicon and another way for
    the second. Neither adjudication belongs to the other's entry, so the second run
    records all six of its items against its own entry -- and the first run's rows come
    out of it byte for byte as they went in. Nothing is rewritten in place, which is what
    "append-only history" has to mean once the entry is the scope of "already recorded".
    """
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    first_plan = _build_plan(root, token="hist", target="p29-hist-a",
                             decisions=_standard_decisions("hist"))
    second_plan = _build_plan(
        root, token="hist", target="p29-hist-b",
        decisions=[_select_supplement("hist"), _exclude_bare("hist")],
        rewrite_sources=False,
    )

    with admin.session() as session:
        first_lexicon = _system_lexicon(session, "p29-hist-a", "p29-test-hist-a")
        second_lexicon = _system_lexicon(session, "p29-hist-b", "p29-test-hist-b")
        administrator = _administrator(session, admin)
        first = confirm_plan(
            session, plan=first_plan, administrator=administrator, source_root=root
        )
        rows_after_first = _evidence_rows(session, first["import_run_id"])
        assert len(rows_after_first) == 6

        second = confirm_plan(
            session, plan=second_plan, administrator=administrator, source_root=root
        )
        # Read the first run's rows again: they must be exactly what they were.
        rows_after_second = _evidence_rows(session, first["import_run_id"])
        second_rows = _evidence_rows(session, second["import_run_id"])
        first_entry = session.query(LexiconEntry).filter_by(
            lexicon_id=first_lexicon.id, normalized_word=_normalized("hist")
        ).one()
        second_entry = session.query(LexiconEntry).filter_by(
            lexicon_id=second_lexicon.id, normalized_word=_normalized("hist")
        ).one()

    assert rows_after_first == rows_after_second
    # The second entry records every item of its own import; the two meanings were
    # decided differently this time, the rest the same way, for the other entry.
    assert second["evidence"]["written"] == len(second_rows) == 6
    assert second["evidence"]["skipped_existing"] == 0
    assert second["evidence"]["readjudicated"] == 0
    assert {row["lexicon_entry_id"] for row in second_rows} == {second_entry.id}
    previous_by_key = {row["evidence_sha256"]: row for row in rows_after_second}
    for row in second_rows:
        previous = previous_by_key[row["evidence_sha256"]]
        assert row["id"] != previous["id"], "a new entry records new rows, never edits"
        assert previous["lexicon_entry_id"] == first_entry.id
    changed = {row["evidence_sha256"]: row for row in second_rows
               if row["field_kind"] == "meaning"}
    assert all(
        changed[key]["decision"] != previous_by_key[key]["decision"] for key in changed
    ), "the differing adjudication is what the second plan decided"
    # Both source texts survive verbatim; neither run replaced the other's values.
    stored = {row["raw_text"] for row in rows_after_second}
    assert {"主词表释义-hist", "补充来源释义-hist"} <= stored
    # The first lexicon's entry was not rewritten by the second run.
    assert first_entry.source_meanings == ["主词表释义-hist"]


def test_no_confirmation_ever_updates_or_deletes_provenance(
    admin, tmp_path: Path
) -> None:
    """Fail loudly if a confirmation ever starts mutating what it recorded.

    The guard covers **every** table 0008 creates and runs across **both** runs, not
    only the second one and not only the evidence table: the confirm path issues no
    ``UPDATE`` and no ``DELETE`` at all, and a narrower guard is exactly how that
    claim stops being true without any test noticing.
    """
    from sqlalchemy import event

    from app.services.public_lexicon_confirm import confirm_plan

    mutating: list[str] = []
    guarded = ("entry_source_evidence", "source_artifact", "public_import_run",
               "public_import_run_source", "lexicon_entry")

    def watch(_conn, _cursor, statement, _params, _context, _many):
        lowered = " ".join(statement.strip().lower().split())
        if not lowered.startswith(("update", "delete")):
            return
        touched = [table for table in guarded if table in lowered]
        if touched:
            mutating.append(f"{lowered[:60]} -> {touched}")

    root = tmp_path / "sources"
    root.mkdir()
    first_plan = _build_plan(root, token="nomut", target="p29-nomut-a",
                             decisions=_standard_decisions("nomut"))

    with admin.session() as session:
        _system_lexicon(session, "p29-nomut-a", "p29-test-nomut-a")
        _system_lexicon(session, "p29-nomut-b", "p29-test-nomut-b")
        administrator = _administrator(session, admin)
        second_plan = _build_plan(
            root, token="nomut", target="p29-nomut-b",
            decisions=[_select_supplement("nomut"), _exclude_bare("nomut")],
            rewrite_sources=False,
        )
        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", watch)
        try:
            confirm_plan(
                session, plan=first_plan, administrator=administrator, source_root=root
            )
            confirm_plan(
                session, plan=second_plan, administrator=administrator, source_root=root
            )
        finally:
            event.remove(bind, "before_cursor_execute", watch)

    assert mutating == []


# --- the operator entry point ------------------------------------------------
#
# The CLI is the first version of the administrator entry point (design section
# 3.3). These tests cover the parts that are the CLI's own responsibility: the
# run-ID confirmation token, the administrator's own current password, and the
# promise that a refusal writes nothing.

#: Matches ``tests/conftest.py``'s TEST_PASSWORD, which is what ``make_world`` sets.
ADMIN_PASSWORD = "test-password-123"


def _cli_argv(root: Path, plan_path: Path, *, confirm: str, admin: str = "p29-admin",
              report: Path | None = None) -> list[str]:
    argv = [
        "public-lexicon", "confirm", "--plan", str(plan_path),
        "--source-root", str(root), "--confirm", confirm, "--admin", admin,
    ]
    if report is not None:
        argv += ["--report", str(report)]
    return argv


def _write_plan_file(root: Path, plan: dict[str, Any]) -> Path:
    from app.services.public_lexicon_plan import write_plan

    path = root / "plan.json"
    write_plan(plan, path)
    return path


def test_cli_refuses_without_the_run_id_token(admin, tmp_path: Path, capsys) -> None:
    from app import cli

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="clitok", target="p29-clitok",
                       decisions=_standard_decisions("clitok"))
    plan_path = _write_plan_file(root, plan)

    with admin.session() as session:
        _system_lexicon(session, "p29-clitok", "p29-test-clitok")
        before = _counts(session)

    assert cli.main(_cli_argv(root, plan_path, confirm="")) == 1
    assert "缺少 --confirm" in capsys.readouterr().out
    with admin.session() as session:
        assert _counts(session) == before


def test_cli_refuses_a_mismatched_run_id_token(admin, tmp_path: Path, capsys) -> None:
    from app import cli

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="clibad", target="p29-clibad",
                       decisions=_standard_decisions("clibad"))
    plan_path = _write_plan_file(root, plan)

    with admin.session() as session:
        _system_lexicon(session, "p29-clibad", "p29-test-clibad")
        before = _counts(session)

    assert cli.main(_cli_argv(root, plan_path, confirm="lex-not-the-run-id")) == 1
    assert "不一致" in capsys.readouterr().out
    with admin.session() as session:
        assert _counts(session) == before


def test_cli_refuses_a_wrong_password_and_writes_nothing(
    admin, tmp_path: Path, capsys, monkeypatch
) -> None:
    from app import cli

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="clipw", target="p29-clipw",
                       decisions=_standard_decisions("clipw"))
    plan_path = _write_plan_file(root, plan)

    with admin.session() as session:
        _system_lexicon(session, "p29-clipw", "p29-test-clipw")
        before = _counts(session)

    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "not-the-password")
    assert cli.main(_cli_argv(root, plan_path, confirm=plan["run_id"])) == 1
    assert "口令不正确" in capsys.readouterr().err
    with admin.session() as session:
        assert _counts(session) == before


def test_cli_refuses_an_unknown_administrator(
    admin, tmp_path: Path, capsys, monkeypatch
) -> None:
    from app import cli

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="clinom", target="p29-clinom",
                       decisions=_standard_decisions("clinom"))
    plan_path = _write_plan_file(root, plan)

    with admin.session() as session:
        _system_lexicon(session, "p29-clinom", "p29-test-clinom")
        before = _counts(session)

    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: ADMIN_PASSWORD)
    assert cli.main(
        _cli_argv(root, plan_path, confirm=plan["run_id"], admin="nobody-here")
    ) == 1
    assert "口令不正确" in capsys.readouterr().err
    with admin.session() as session:
        assert _counts(session) == before


def test_cli_refuses_a_non_admin_account(
    admin, make_world, tmp_path: Path, capsys, monkeypatch
) -> None:
    from app import cli

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="cliuser", target="p29-cliuser",
                       decisions=_standard_decisions("cliuser"))
    plan_path = _write_plan_file(root, plan)
    plain = make_world("p29-plain-cli")

    try:
        with admin.session() as session:
            _system_lexicon(session, "p29-cliuser", "p29-test-cliuser")
            before = _counts(session)

        monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: ADMIN_PASSWORD)
        assert cli.main(
            _cli_argv(root, plan_path, confirm=plan["run_id"], admin="p29-plain-cli")
        ) == 1
        assert "不是管理员" in capsys.readouterr().err
        with admin.session() as session:
            assert _counts(session) == before
    finally:
        plain.client.__exit__(None, None, None)


def test_cli_confirms_and_reports_what_it_wrote(
    admin, tmp_path: Path, capsys, monkeypatch
) -> None:
    from app import cli
    from app.models import LexiconEntry

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="clihap", target="p29-clihap",
                       decisions=_standard_decisions("clihap"))
    plan_path = _write_plan_file(root, plan)

    with admin.session() as session:
        lexicon = _system_lexicon(session, "p29-clihap", "p29-test-clihap")
        before = _counts(session)

    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: ADMIN_PASSWORD)
    assert cli.main(_cli_argv(root, plan_path, confirm=plan["run_id"])) == 0
    printed = capsys.readouterr().out
    assert "已确认并提交" in printed
    assert plan["run_id"] in printed

    with admin.session() as session:
        after = _counts(session)
        entry = session.query(LexiconEntry).filter_by(
            lexicon_id=lexicon.id, normalized_word=_normalized("clihap")
        ).one()
    assert after["lexicon_entry"] == before["lexicon_entry"] + 1
    assert after["user_word_state"] == before["user_word_state"]
    assert after["review_event"] == before["review_event"]
    assert entry.source_meanings == ["主词表释义-clihap"]


def test_cli_writes_a_failure_report_outside_the_transaction(
    admin, tmp_path: Path, capsys, monkeypatch
) -> None:
    from app import cli
    from app.services import public_lexicon_confirm as service

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="clirep", target="p29-clirep",
                       decisions=_standard_decisions("clirep"))
    plan_path = _write_plan_file(root, plan)
    report = tmp_path / "failure.json"

    with admin.session() as session:
        _system_lexicon(session, "p29-clirep", "p29-test-clirep")
        before = _counts(session)

    def explode(*_args, **_kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(service, "_plan_evidence", explode)
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: ADMIN_PASSWORD)
    assert cli.main(
        _cli_argv(root, plan_path, confirm=plan["run_id"], report=report)
    ) == 1

    written = json.loads(report.read_text(encoding="utf-8"))
    assert written["committed"] is False
    assert written["plan_sha256"] == plan["plan_sha256"]
    assert written["administrator"] == "p29-admin"
    with admin.session() as session:
        assert _counts(session) == before

    # A second failure must not overwrite the first report.
    assert cli.main(
        _cli_argv(root, plan_path, confirm=plan["run_id"], report=report)
    ) == 1
    assert json.loads(report.read_text(encoding="utf-8")) == written


# --- the declared provenance reaches the row, or nothing is written ----------
#
# Three layers guard the same rule, and each is tested separately because they fail
# in different circumstances: the plan blocks an incomplete declaration (so the
# operator sees it before confirming), the service refuses it with an actionable
# message (so a plan whose digest was recomputed by hand still cannot get through),
# and the table refuses a blank row (so no future code path can create one).


def test_confirm_records_the_declared_provenance(admin, tmp_path: Path) -> None:
    from app.models import PublicImportRunSource, SourceArtifact
    from app.services.public_lexicon_confirm import confirm_plan

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="prov", target="p29-prov",
                       decisions=_standard_decisions("prov"))

    with admin.session() as session:
        _system_lexicon(session, "p29-prov", "p29-test-prov")
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        artifacts = {
            row.role: row
            for row in session.query(SourceArtifact).join(
                PublicImportRunSource,
                PublicImportRunSource.source_artifact_id == SourceArtifact.id,
            ).filter(PublicImportRunSource.import_run_id == result["import_run_id"]).all()
        }

    assert set(artifacts) == {"primary", "meaning"}
    for artifact in artifacts.values():
        assert artifact.publisher == "synthetic publisher"
        assert artifact.version == "2026-09-25"
        assert artifact.obtained_at_utc == "2026-09-25T00:00:00Z"
        assert artifact.license_id == "synthetic-test-only"
        assert artifact.use_scope == "local-evaluation"
        assert artifact.display_scope == "not-for-publication"
        assert artifact.storage_locator.startswith("sources/")
        assert artifact.format == "delimited-text-v1"
        assert artifact.file_sha256 and artifact.mapping_sha256
    # The locator is the declared one, not a fallback the writer invented.
    assert artifacts["primary"].storage_locator == "sources/primary.csv"


def test_confirm_refuses_reuse_when_declared_license_changes(admin, tmp_path: Path) -> None:
    """A later run must not attribute a changed license to an older artifact."""
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="licensechange", target="p29-licensechange",
                       decisions=_standard_decisions("licensechange"))
    with admin.session() as session:
        _system_lexicon(session, "p29-licensechange", "p29-test-licensechange")
        administrator = _administrator(session, admin)
        confirm_plan(session, plan=plan, administrator=administrator, source_root=root)
        before = _counts(session)
        revised = json.loads(json.dumps(plan))
        revised["sources"][0]["provenance"]["license_id"] = "corrected-license"
        revised["plan_sha256"] = plan_digest(revised)

        with pytest.raises(ConfirmRefused, match="授权元数据.*不一致"):
            confirm_plan(
                session, plan=revised, administrator=administrator, source_root=root,
            )
        assert _counts(session) == before


def test_confirm_refuses_a_plan_with_incomplete_provenance(
    admin, tmp_path: Path
) -> None:
    """Bypass the plan's own blocker: the service must still refuse."""
    from app.services.public_lexicon_confirm import ConfirmRefused, confirm_plan
    from app.services.public_lexicon_plan import plan_digest

    root = tmp_path / "sources"
    root.mkdir()
    plan = _build_plan(root, token="provgap", target="p29-provgap",
                       decisions=_standard_decisions("provgap"))
    for source in plan["sources"]:
        source["provenance"]["license_id"] = ""
        source["missing_provenance"] = ["license_id"]
    # Someone recomputed the digest and cleared the blockers by hand. The service
    # still has to refuse, because the row it would write has no licence.
    plan["confirmation_blockers"] = []
    plan["confirmation_ready"] = True
    plan["plan_sha256"] = plan_digest(plan)

    with admin.session() as session:
        _system_lexicon(session, "p29-provgap", "p29-test-provgap")
        before = _counts(session)
        with pytest.raises(ConfirmRefused, match="授权元数据不完整"):
            confirm_plan(
                session, plan=plan, administrator=_administrator(session, admin),
                source_root=root,
            )
        after = _counts(session)

    assert after == before


def test_the_database_refuses_an_artifact_with_blank_provenance(
    admin, tmp_path: Path
) -> None:
    """The last layer: a blank row cannot be inserted by any path at all."""
    from sqlalchemy.exc import IntegrityError

    from app.models import SourceArtifact

    with admin.session() as session:
        session.add(SourceArtifact(
            role="primary",
            name="blank.csv",
            # Every required provenance field left empty.
            format="delimited-text-v1",
            mapping_json="{}",
            mapping_sha256="0" * 64,
            file_sha256="1" * 64,
            byte_size=1,
        ))
        with pytest.raises(IntegrityError, match="ck_source_artifact_provenance_present"):
            session.commit()
        session.rollback()
        assert session.query(SourceArtifact).filter_by(name="blank.csv").count() == 0
