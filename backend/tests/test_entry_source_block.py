"""Per-field source provenance on ``GET /api/words/state/{id}``.

The word detail route answers *where each displayed value came from*: which source
artifact and line, whether a human adopted that row, and a link to the exact pinned
revision it was read at. The properties under test are the ones that make such a block
worth showing at all.

* A field's source is the source *of that field*, so one word's fields may legitimately
  come from two artifacts at two revisions, and each row's link comes from its own
  artifact's frozen mapping rather than from whichever one happened to be read first.
* A row the import recorded but nobody adopted is not the source of a displayed value.
  It stays visible under ``candidates``, which is the difference between "here is what
  we did not take" and "this word's meaning came from there".
* A link that cannot be built honestly is not built. An empty or unusable revision, or
  a mapping that declares no usable template, answers an empty string; nothing falls
  back to a line number and nothing produces half a URL.
* The block is read from the database alone. Moving the source archive away changes
  nothing, which is the whole reason the revision was frozen into the evidence row.
* What a reader may see is bounded: no local archive path, no frozen mapping, no
  operator identity, and no other entry's evidence.

Every import here is a real confirmation through ``confirm_plan`` against synthetic CSV
in a temporary directory, on the session's migrated 0010 database. No test opens a file
under ``data/`` and none touches the production database.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from tests.test_public_lexicon_confirm import _administrator, _system_lexicon
from tests.test_public_lexicon_plan import provenance_block

#: Two *different* templates, one per source. If the read path resolved the template
#: once per entry instead of once per row's own artifact, one of the two links below
#: would come out with the other source's shape -- which is why they differ.
PRIMARY_TEMPLATE = "https://zh.wiktionary.org/w/index.php?oldid={revision}"
SUPPLEMENT_TEMPLATE = "https://github.com/example/supplement-pack/tree/{revision}"

PRIMARY_REVISION = "1111111"
SUPPLEMENT_REVISION = "2222222"

#: The whole of what one evidence row may expose. Named rather than counted, so
#: widening the payload has to be a deliberate edit here too.
EVIDENCE_FIELDS = frozenset({
    "source_evidence_id", "field_kind", "row_locator", "sense_key", "raw_text",
    "decision", "selected_for_default", "selection_order", "source_revision",
    "source_revision_url", "source",
})

#: The whole of what one source may expose about itself. ``storage_locator`` is the
#: archive's own local path and ``mapping_json`` is the frozen reader configuration;
#: ``entry_source_evidence.evidence_sha256`` is an internal idempotency key. None of
#: them is needed to show a reader where a value came from.
SOURCE_FIELDS = frozenset({
    "source_artifact_id", "role", "name", "publisher", "version", "license_id",
})

FORBIDDEN_KEYS = frozenset({
    "storage_locator", "mapping_json", "mapping", "file_path", "path",
    "plan_sha256", "plan_type", "evidence_sha256", "file_sha256", "mapping_sha256",
    "confirmed_by_user_id", "confirmed_by_username", "confirmed_at", "normalized_word",
})


@pytest.fixture()
def admin(make_world):
    """An administrator who performs the confirmations that create the records."""
    value = make_world("esb-admin", role="admin")
    yield value
    value.client.__exit__(None, None, None)


# --- synthetic sources -------------------------------------------------------


def _primary_text(token: str, revision: str = PRIMARY_REVISION) -> str:
    """Two words in the primary source, each row carrying its own revision."""
    return (
        "head,cn,oldid\n"
        f"Word{token},主词表释义-{token},{revision}\n"
        f"Other{token},另一词释义-{token},3333333\n"
    )


def _supplement_text(token: str, revision: str = SUPPLEMENT_REVISION) -> str:
    """The same two words in a second source, at a second revision."""
    return (
        "term,translation,ipa,pos,rev\n"
        f" word{token} ,补充释义-{token},/ˈ{token}/,noun,{revision}\n"
        f" other{token} ,另一词补充-{token},/ˈo{token}/,adj,4444444\n"
    )


def _manifest(
    root: Path,
    *,
    primary_template: str = PRIMARY_TEMPLATE,
    supplement_template: str = SUPPLEMENT_TEMPLATE,
) -> Path:
    """Both sources declare where their rows' revisions come from, and how to link."""
    path = root / "manifest.json"
    path.write_text(json.dumps({
        "required_fields": ["meaning"],
        "sources": [
            {"id": "primary", "role": "primary", "file": "primary.csv",
             "columns": {"word": "head", "meaning": "cn"},
             "revision": {"column": "oldid", "url_template": primary_template},
             "provenance": provenance_block("primary")},
            {"id": "supplement", "role": "meaning", "file": "supplement.csv",
             "columns": {"word": "term", "meaning": "translation",
                         "phonetic": "ipa", "part_of_speech": "pos"},
             "revision": {"column": "rev", "url_template": supplement_template},
             "provenance": provenance_block("supplement")},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _decisions(token: str) -> list[dict[str, Any]]:
    """Adopt the *supplement's* meaning for one word and the primary's for the other.

    Two words, two decisions, and neither adopts every field from one source: the first
    word's meaning comes from the supplement, so the primary's own meaning for it is
    recorded as a candidate the import did not take.
    """
    return [
        {"normalized_word": f"word{token}", "field": "meaning", "action": "select",
         "evidence": [{"source_id": "supplement", "line": 2}],
         "note": "以补充来源释义为默认"},
        {"normalized_word": f"other{token}", "field": "meaning", "action": "select",
         "evidence": [{"source_id": "primary", "line": 3}],
         "note": "以主词表释义为默认"},
    ]


def _decisions_path(root: Path, entries: list[dict[str, Any]]) -> Path:
    path = root / "decisions.json"
    path.write_text(
        json.dumps({"format_version": 1, "decisions": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _import_two_words(
    admin,
    root: Path,
    *,
    token: str,
    lexicon_name: str,
    source_type: str | None = None,
    primary_revision: str = PRIMARY_REVISION,
    supplement_revision: str = SUPPLEMENT_REVISION,
) -> dict[str, Any]:
    """One real confirmation of a two-word source pair, plus this admin's states.

    Returns the entry and ``user_word_state`` ids by normalized word, so a test can ask
    the API for exactly the entry it means. Calling it twice with one token writes the
    same bytes twice, which is how a second target re-imports one source.
    """
    from app.models import LexiconEntry
    from app.services.public_lexicon_confirm import confirm_plan
    from app.services.public_lexicon_plan import build_plan
    from app.services.userdata import get_or_create_word_state

    (root / "primary.csv").write_text(_primary_text(token, primary_revision), encoding="utf-8")
    (root / "supplement.csv").write_text(
        _supplement_text(token, supplement_revision), encoding="utf-8"
    )
    plan = build_plan(
        manifest_path=_manifest(root),
        source_root=root,
        decisions_path=_decisions_path(root, _decisions(token)),
        target_lexicon=lexicon_name,
    )

    with admin.session() as session:
        # ``uq_lexicon_system`` allows one system lexicon per source type, so a second
        # target needs its own.
        lexicon = _system_lexicon(
            session, lexicon_name, source_type or f"esb-{token}"
        )
        result = confirm_plan(
            session, plan=plan, administrator=_administrator(session, admin),
            source_root=root,
        )
        assert result["status"] == "applied", result
        administrator = _administrator(session, admin)
        entries = session.scalars(
            select(LexiconEntry).where(LexiconEntry.lexicon_id == lexicon.id)
        ).all()
        ids: dict[str, Any] = {"entries": {}, "states": {}}
        for entry in entries:
            ids["entries"][entry.normalized_word] = entry.id
            ids["states"][entry.normalized_word] = get_or_create_word_state(
                session, administrator, entry
            ).id
        ids["lexicon_id"] = lexicon.id
        ids["run_id"] = result["import_run_id"]
        session.commit()
    return ids


def _detail(world, state_id: int) -> dict[str, Any]:
    response = world.client.get(f"/api/words/state/{state_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _block(world, state_id: int) -> dict[str, Any]:
    return _detail(world, state_id)["sources"]


def _field(block: dict[str, Any], kind: str) -> dict[str, Any]:
    for item in block["fields"]:
        if item["field_kind"] == kind:
            return item
    raise AssertionError(f"no {kind!r} field in the source block: {block['fields']}")


def _only(items: list[dict[str, Any]], *, adopted: bool) -> dict[str, Any]:
    assert len(items) == 1, f"expected exactly one row, got {items}"
    assert items[0]["selected_for_default"] is adopted
    return items[0]


def _all_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _all_keys(item)
    return found


def _all_strings(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_all_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_all_strings(item))
    elif isinstance(value, str):
        found.append(value)
    return found


def _without_row_ids(block: dict[str, Any]) -> dict[str, Any]:
    """The block with each row's own record id blanked.

    Two entries written from the same source and the same decision hold the same
    *content* and their own, different evidence rows. Comparing the ids would only
    report that, so they are removed and the ids are compared as sets elsewhere.
    """
    copied = json.loads(json.dumps(block))
    for field in copied["fields"]:
        for row in [*field["selected"], *field["candidates"]]:
            row["source_evidence_id"] = None
    return copied


# --- one field, one source ---------------------------------------------------


def test_each_field_carries_the_source_and_revision_of_its_own_row(
    admin, tmp_path: Path
) -> None:
    """Same word, different fields, different sources -- and different links.

    The word itself is adopted from the primary source while its meaning, phonetic and
    part of speech are adopted from the supplement. Each row's URL has to come from the
    artifact *that row* names: a single per-entry template would put the primary's
    ``oldid`` shape on the supplement's row and look perfectly plausible.
    """
    root = tmp_path / "sources"
    root.mkdir()
    token = "esbmix"
    ids = _import_two_words(admin, root, token=token, lexicon_name="esb-mix")

    detail = _detail(admin, ids["states"][f"word{token}"])
    block = detail["sources"]

    word = _only(_field(block, "word")["selected"], adopted=True)
    assert word["source"]["role"] == "primary"
    assert word["raw_text"] == f"Word{token}"
    assert word["source_revision"] == PRIMARY_REVISION
    assert word["source_revision_url"] == PRIMARY_TEMPLATE.format(revision=PRIMARY_REVISION)
    assert word["row_locator"] == 2

    meaning = _only(_field(block, "meaning")["selected"], adopted=True)
    assert meaning["source"]["role"] == "meaning"
    assert meaning["raw_text"] == f"补充释义-{token}"
    assert meaning["source_revision"] == SUPPLEMENT_REVISION
    assert meaning["source_revision_url"] == SUPPLEMENT_TEMPLATE.format(
        revision=SUPPLEMENT_REVISION
    )
    assert meaning["row_locator"] == 2

    for kind in ("phonetic", "part_of_speech"):
        row = _only(_field(block, kind)["selected"], adopted=True)
        assert row["source"]["role"] == "meaning", "both only exist in the supplement"
        assert row["source_revision"] == SUPPLEMENT_REVISION
        assert row["source_revision_url"] == SUPPLEMENT_TEMPLATE.format(
            revision=SUPPLEMENT_REVISION
        )

    assert block["completeness"]["status"] == "complete", block["completeness"]

    # The untouched source fields and the adopted meanings still say what they said.
    assert detail["source_raw"] == f"Word{token},主词表释义-{token},{PRIMARY_REVISION}"
    assert detail["source_meanings"] == [f"补充释义-{token}"]
    assert detail["word"] == f"Word{token}"
    assert detail["status"] == "new"


def test_a_row_the_import_did_not_adopt_is_not_the_source_of_the_value(
    admin, tmp_path: Path
) -> None:
    """Only an adopted row may stand beside a displayed value.

    The import wrote a row for the primary's meaning as well, and a human picked the
    supplement's instead. Both stay visible -- but in different lists, so a reader
    cannot take the primary's line for the origin of the displayed meaning.
    """
    root = tmp_path / "sources"
    root.mkdir()
    token = "esbcand"
    ids = _import_two_words(admin, root, token=token, lexicon_name="esb-cand")

    detail = _detail(admin, ids["states"][f"word{token}"])
    meaning = _field(detail["sources"], "meaning")

    adopted = [row for row in meaning["selected"]]
    assert [row["raw_text"] for row in adopted] == [f"补充释义-{token}"], (
        "the displayed source_meanings must be the adopted row's text"
    )
    assert detail["source_meanings"] == [row["raw_text"] for row in adopted]

    candidates = [row["raw_text"] for row in meaning["candidates"]]
    assert f"主词表释义-{token}" in candidates, (
        "the row a human saw and did not take has to stay visible as a candidate"
    )
    assert all(row["selected_for_default"] is False for row in meaning["candidates"])
    assert all(row["decision"] == "not_selected" for row in meaning["candidates"])
    assert all(row["selection_order"] is None for row in meaning["candidates"])
    assert f"主词表释义-{token}" not in [row["raw_text"] for row in adopted], (
        "an unused source must never be presented as the origin of the display value"
    )

    # The word field shows the same split from the other side: the supplement's
    # spelling was recorded and not taken, because the primary's own spelling won.
    word = _field(detail["sources"], "word")
    assert [row["source"]["role"] for row in word["selected"]] == ["primary"]
    assert [row["source"]["role"] for row in word["candidates"]] == ["meaning"]


def test_the_concise_meaning_reports_the_evidence_row_it_was_proposed_against(
    admin, tmp_path: Path
) -> None:
    """``concise_meanings[]`` carries the id that ties it to one evidence row.

    The display value and the source block have to agree about which row a short
    meaning came from; the id is what lets a client ask, and a supplement has none.
    """
    from app.services.concise_meaning import (
        KIND_AI_SUPPLEMENT,
        KIND_DERIVED,
        ConciseMeaningProposal,
        confirm,
        propose,
    )

    root = tmp_path / "sources"
    root.mkdir()
    token = "esbcm"
    ids = _import_two_words(admin, root, token=token, lexicon_name="esb-cm")
    entry_id = ids["entries"][f"word{token}"]
    state_id = ids["states"][f"word{token}"]

    with admin.session() as session:
        from app.models import EntrySourceEvidence, LexiconEntry

        evidence = session.scalars(
            select(EntrySourceEvidence).where(
                EntrySourceEvidence.lexicon_entry_id == entry_id,
                EntrySourceEvidence.field_kind == "meaning",
                EntrySourceEvidence.selected_for_default.is_(True),
            )
        ).all()
        assert len(evidence) == 1, "control: exactly one adopted meaning row"
        evidence_id = evidence[0].id
        administrator = _administrator(session, admin)
        entry = session.get(LexiconEntry, entry_id)
        rows = propose(
            session, entry=entry,
            proposals=[
                ConciseMeaningProposal(
                    text=f"补充释义-{token}", provenance_kind=KIND_DERIVED,
                    display_order=1, source_locator="meaning:2",
                    derivation_note="来源为补充来源原文，此处仅取常见义项",
                    source_evidence_id=evidence_id,
                ),
                ConciseMeaningProposal(
                    text="自拟补充义项", provenance_kind=KIND_AI_SUPPLEMENT,
                    display_order=2, derivation_note="来源未收录该义项，按常用度补足",
                ),
            ],
            actor=administrator,
        )
        for row in rows:
            confirm(session, meaning=row, confirmer=administrator)
        session.commit()

    detail = _detail(admin, state_id)
    by_order = {item["display_order"]: item for item in detail["concise_meanings"]}
    assert by_order[1]["source_evidence_id"] == evidence_id
    assert by_order[2]["source_evidence_id"] is None, (
        "a supplement points at no evidence row"
    )

    # And the id means the same thing on both sides of the payload.
    adopted = _field(detail["sources"], "meaning")["selected"][0]
    assert adopted["source_evidence_id"] == by_order[1]["source_evidence_id"]


# --- no link rather than a wrong link ----------------------------------------


def test_an_empty_revision_cell_answers_an_empty_link(admin, tmp_path: Path) -> None:
    """A word with no pinned revision upstream gets the honest blank, not a guess.

    The empty cell is a legal input and means "no revision known". It must not fall
    back to the row number, and the payload has to say which item is missing instead of
    quietly showing a link-shaped field.
    """
    root = tmp_path / "sources"
    root.mkdir()
    token = "esbempty"
    ids = _import_two_words(
        admin, root, token=token, lexicon_name="esb-empty", primary_revision=""
    )

    block = _block(admin, ids["states"][f"word{token}"])
    word = _only(_field(block, "word")["selected"], adopted=True)
    assert word["source_revision"] == ""
    assert word["source_revision_url"] == "", "an empty revision never becomes a link"

    meaning = _only(_field(block, "meaning")["selected"], adopted=True)
    assert meaning["source_revision_url"] == SUPPLEMENT_TEMPLATE.format(
        revision=SUPPLEMENT_REVISION
    ), "the other source's row is unaffected"

    missing = [item for item in block["completeness"]["missing"]
               if item["code"] == "source_revision_missing"]
    assert [item["field_kind"] for item in missing] == ["word"]
    assert block["completeness"]["status"] == "incomplete"
    assert "固定修订号" in block["completeness"]["message"]
    assert [item["code"] for item in block["completeness"]["missing"]] == [
        "source_revision_missing"
    ], "the supplement's row is complete and must not be reported as missing"


@pytest.mark.parametrize(
    ("case", "payload"),
    [
        ("unparseable", "this is not json"),
        ("norevkey", json.dumps({"columns": {"word": "head"}})),
        ("nothttps", json.dumps(
            {"revision": {"column": "oldid", "value": "",
                          "url_template": "http://zh.wiktionary.org/{revision}"}}
        )),
        ("twoplace", json.dumps(
            {"revision": {"column": "oldid", "value": "",
                          "url_template": "https://example.test/{revision}{revision}"}}
        )),
        ("bothforms", json.dumps(
            {"revision": {"column": "oldid", "value": "70dc6b68",
                          "url_template": "https://example.test/{revision}"}}
        )),
        ("whitespace", json.dumps(
            {"revision": {"column": "oldid", "value": "",
                          "url_template": "https://example.test/a b/{revision}"}}
        )),
    ],
)
def test_an_unusable_frozen_mapping_answers_an_empty_link(
    admin, tmp_path: Path, case: str, payload: str
) -> None:
    """A mapping with no usable template degrades, and it degrades alone.

    The frozen mapping is validated by the same :class:`RevisionDeclaration` the
    manifest was accepted under, so a stored configuration the read path cannot use --
    whatever made it unusable -- produces an empty string rather than a partial URL.
    Only the tampered artifact's rows are affected: the other source keeps its link, and
    the revision text stays visible so "no revision" and "cannot link" stay distinct.
    """
    from app.models import EntrySourceEvidence, SourceArtifact

    root = tmp_path / "sources"
    root.mkdir()
    token = f"esbbad{case}"
    ids = _import_two_words(admin, root, token=token, lexicon_name=f"esb-bad-{case}")

    with admin.session() as session:
        primary_row = session.scalars(
            select(EntrySourceEvidence).where(
                EntrySourceEvidence.lexicon_entry_id == ids["entries"][f"word{token}"],
                EntrySourceEvidence.field_kind == "word",
            )
        ).first()
        artifact = session.get(SourceArtifact, primary_row.source_artifact_id)
        assert PRIMARY_TEMPLATE in artifact.mapping_json, (
            "control: the archived mapping really did declare a usable template"
        )
        artifact.mapping_json = payload
        session.commit()

    block = _block(admin, ids["states"][f"word{token}"])
    word = _only(_field(block, "word")["selected"], adopted=True)
    assert word["source_revision"] == PRIMARY_REVISION, (
        "the revision is still reported: the reader is told what we know"
    )
    assert word["source_revision_url"] == "", "no partial URL may be built"

    meaning = _only(_field(block, "meaning")["selected"], adopted=True)
    assert meaning["source_revision_url"] == SUPPLEMENT_TEMPLATE.format(
        revision=SUPPLEMENT_REVISION
    ), "the other artifact's mapping is untouched"

    codes = [(item["code"], item["field_kind"]) for item in block["completeness"]["missing"]]
    assert ("source_revision_url_unavailable", "word") in codes
    assert ("source_revision_url_unavailable", "meaning") not in codes
    assert block["completeness"]["status"] == "incomplete"


@pytest.mark.parametrize(
    ("revision", "linked"),
    [
        pytest.param("6588944", True, id="ordinary-revision"),
        pytest.param("70dc6b68c855f21e666a7a291ff8ead5ca1f7b44", True, id="commit-hash"),
        pytest.param("", False, id="empty"),
        pytest.param("   ", False, id="spaces-only"),
        pytest.param("65 88944", False, id="interior-space"),
        pytest.param("6588\n944", False, id="newline"),
        pytest.param("6" * 65, False, id="longer-than-the-column"),
    ],
)
def test_only_a_revision_that_is_an_identifier_becomes_a_link(
    revision: str, linked: bool
) -> None:
    """The link builder is the single gate, and it is boring on purpose."""
    from app.services.entry_provenance import revision_url

    url = revision_url(PRIMARY_TEMPLATE, revision)
    if linked:
        assert url == PRIMARY_TEMPLATE.format(revision=revision)
    else:
        assert url == "", f"a revision that is not an identifier must not be linked: {url!r}"


def test_a_revision_cannot_reshape_the_url_it_enters() -> None:
    """Percent-encoding keeps a stored value from adding a query or a fragment."""
    from app.services.entry_provenance import revision_url

    assert revision_url(PRIMARY_TEMPLATE, "a?b=c#d") == (
        "https://zh.wiktionary.org/w/index.php?oldid=a%3Fb%3Dc%23d"
    )
    assert revision_url(PRIMARY_TEMPLATE, "a/b") == (
        "https://zh.wiktionary.org/w/index.php?oldid=a%2Fb"
    )


def test_a_template_that_is_not_declared_at_all_answers_an_empty_link() -> None:
    """A source that declares no revision is an ordinary answer, not an error."""
    from app.services.entry_provenance import frozen_revision_template, revision_url

    class _Artifact:
        """The one attribute the read path uses."""

        def __init__(self, mapping_json: str) -> None:
            self.mapping_json = mapping_json

    assert frozen_revision_template(_Artifact('{"columns": {"word": "head"}}')) == ""
    assert frozen_revision_template(_Artifact("")) == ""
    assert revision_url("", PRIMARY_REVISION) == ""


# --- entries and accounts the block must not mix -----------------------------


def test_an_entry_with_no_source_record_answers_an_empty_block(admin) -> None:
    """A word from before any import degrades in words, and the page still works."""
    state_id, entry_id = admin.add_word(
        "esboldword", source_meanings=["旧数据释义"], source_raw="esboldword 旧数据释义"
    )

    detail = _detail(admin, state_id)
    assert detail["lexicon_entry_id"] == entry_id
    block = detail["sources"]
    assert block["fields"] == [], "no evidence rows means no fields to describe"
    assert block["completeness"]["status"] == "incomplete"
    assert [item["code"] for item in block["completeness"]["missing"]] == ["no_evidence"]
    assert block["completeness"]["message"]
    # The values themselves are untouched by the degradation.
    assert detail["source_meanings"] == ["旧数据释义"]
    assert detail["source_raw"] == "esboldword 旧数据释义"


def test_a_second_lexicon_records_its_own_evidence_for_the_same_source(
    admin, tmp_path: Path
) -> None:
    """The same file, the same words, the same decisions -- into two public lexicons.

    The evidence key covers the file bytes, the locator, the field and the value; it
    deliberately does not cover the entry. A deduplication that reads "this source
    value was already adjudicated" from a row belonging to *another* lexicon's word
    therefore skips every row of the second import, and the second entry ends up with
    no source record at all: its detail page answers ``no_evidence`` for content the
    import really did write.

    Each entry's rows have to belong to that entry, so both words must answer the same
    complete per-field block -- and the artifact is still reused by fingerprint rather
    than re-created under a new label.
    """
    from app.models import EntrySourceEvidence, PublicImportRunSource

    root = tmp_path / "sources"
    root.mkdir()
    token = "esbtwice"
    word = f"word{token}"
    first = _import_two_words(admin, root, token=token, lexicon_name="esb-twice-a")
    second = _import_two_words(
        admin, root, token=token, lexicon_name="esb-twice-b",
        source_type=f"esb-{token}-second",
    )

    assert first["entries"][word] != second["entries"][word], (
        "control: the two targets hold two different entries"
    )

    with admin.session() as session:
        def artifacts_of(run_id: int) -> list[int]:
            return sorted(session.scalars(
                select(PublicImportRunSource.source_artifact_id).where(
                    PublicImportRunSource.import_run_id == run_id
                )
            ).all())

        rows_a = session.scalars(
            select(EntrySourceEvidence).where(
                EntrySourceEvidence.lexicon_entry_id == first["entries"][word]
            )
        ).all()
        rows_b = session.scalars(
            select(EntrySourceEvidence).where(
                EntrySourceEvidence.lexicon_entry_id == second["entries"][word]
            )
        ).all()
        assert artifacts_of(second["run_id"]) == artifacts_of(first["run_id"]), (
            "the artifact is reused by fingerprint, not re-created for the new target"
        )

    assert rows_a, "control: the first entry has its own per-field evidence"
    assert rows_b, (
        "the second lexicon's entry must have per-field evidence of its own, not none"
    )
    assert len(rows_b) == len(rows_a)
    assert not ({row.id for row in rows_a} & {row.id for row in rows_b}), (
        "each entry's rows are its own records"
    )
    assert {row.lexicon_entry_id for row in rows_b} == {second["entries"][word]}

    block_a = _block(admin, first["states"][word])
    block_b = _block(admin, second["states"][word])
    assert block_b["fields"], (
        "the second entry's detail page must show its sources, not an empty block"
    )
    assert block_b["completeness"]["status"] == "complete", block_b["completeness"]
    assert _without_row_ids(block_b) == _without_row_ids(block_a), (
        "same source and same decision, the same block -- each entry holding its own rows"
    )
    ids_a = {
        row["source_evidence_id"]
        for field in block_a["fields"] for row in [*field["selected"], *field["candidates"]]
    }
    ids_b = {
        row["source_evidence_id"]
        for field in block_b["fields"] for row in [*field["selected"], *field["candidates"]]
    }
    assert ids_a and ids_b and not ids_a & ids_b

    # And the link a reader follows is the pinned revision of the row it came from.
    adopted = _only(_field(block_b, "word")["selected"], adopted=True)
    assert adopted["source_revision"] == PRIMARY_REVISION
    assert adopted["source_revision_url"] == PRIMARY_TEMPLATE.format(
        revision=PRIMARY_REVISION
    )


def test_the_block_holds_only_this_entry_s_evidence(admin, tmp_path: Path) -> None:
    """Two entries of one import: neither may show the other's rows."""
    from app.models import EntrySourceEvidence

    root = tmp_path / "sources"
    root.mkdir()
    token = "esbscope"
    ids = _import_two_words(admin, root, token=token, lexicon_name="esb-scope")

    with admin.session() as session:
        mine = session.scalars(
            select(EntrySourceEvidence.id).where(
                EntrySourceEvidence.lexicon_entry_id == ids["entries"][f"word{token}"]
            )
        ).all()
        theirs = session.scalars(
            select(EntrySourceEvidence.id).where(
                EntrySourceEvidence.lexicon_entry_id == ids["entries"][f"other{token}"]
            )
        ).all()
    assert mine and theirs and not set(mine) & set(theirs)

    block = _block(admin, ids["states"][f"word{token}"])
    seen = {
        row["source_evidence_id"]
        for field in block["fields"]
        for row in [*field["selected"], *field["candidates"]]
    }
    assert seen == set(mine)
    assert not seen & set(theirs)
    texts = _all_strings(block)
    assert f"另一词补充-{token}" not in texts
    assert f"另一词释义-{token}" not in texts


def test_another_account_cannot_read_the_state_or_the_source_block(
    admin, make_world, tmp_path: Path
) -> None:
    """The block adds no way to reach another user's word.

    The namespace is unchanged: another account's ``user_word_state.id`` is a 404 whose
    body carries nothing about the entry. And because the sources describe the shared
    entry rather than anyone's learning state, two accounts studying the same word see
    the same block -- which is the intended behaviour, not a leak.
    """
    from app.services.userdata import get_or_create_word_state

    root = tmp_path / "sources"
    root.mkdir()
    token = "esbbound"
    ids = _import_two_words(admin, root, token=token, lexicon_name="esb-bound")
    entry_id = ids["entries"][f"word{token}"]
    admin_state = ids["states"][f"word{token}"]

    member = make_world("esb-member")
    try:
        refused = member.client.get(f"/api/words/state/{admin_state}")
        assert refused.status_code == 404, refused.text
        body = refused.text
        for leak in (
            f"Word{token}", f"补充释义-{token}", str(PRIMARY_REVISION),
            "primary", "supplement", "source_evidence_id",
        ):
            assert leak not in body, f"the refusal leaks {leak!r}"

        with admin.session() as session:
            from app.models import LexiconEntry

            entry = session.get(LexiconEntry, entry_id)
            member_state = get_or_create_word_state(
                session, _administrator(session, member), entry
            ).id
            session.commit()

        mine = _block(admin, admin_state)
        theirs = _block(member, member_state)
        assert mine == theirs, "the source record is the entry's, not the account's"
    finally:
        member.client.__exit__(None, None, None)


def test_the_block_names_no_local_path_and_no_internal_record(
    admin, tmp_path: Path
) -> None:
    """What a reader gets is the declaration, not where this machine keeps the file."""
    root = tmp_path / "sources"
    root.mkdir()
    token = "esbsafe"
    ids = _import_two_words(admin, root, token=token, lexicon_name="esb-safe")

    block = _block(admin, ids["states"][f"word{token}"])
    for field in block["fields"]:
        for row in [*field["selected"], *field["candidates"]]:
            assert set(row) == EVIDENCE_FIELDS, f"unexpected evidence fields: {set(row)}"
            assert set(row["source"]) == SOURCE_FIELDS
            assert row["source"]["publisher"]

    assert not (_all_keys(block) & FORBIDDEN_KEYS)

    strings = _all_strings(block)
    for text in strings:
        assert "\\" not in text, f"a Windows path separator appeared in {text!r}"
        assert str(tmp_path) not in text
        assert str(root) not in text
        for drive in ("C:", "D:"):
            assert drive not in text, f"a drive letter appeared in {text!r}"
    assert "primary.csv" in strings, "control: the declared file name is still shown"


def test_the_legacy_route_is_unchanged(admin) -> None:
    """Only the entry detail route reads sources; ``/api/words/{id}`` answers as before."""
    state_id, entry_id, legacy_id = admin.add_legacy_word("esblegacy")

    response = admin.client.get(f"/api/words/{legacy_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "sources" not in body, "the legacy route gained no new block"
    assert body["lexicon_entry_id"] == entry_id
    assert body["word_state_id"] == state_id


def test_the_answer_is_the_same_after_the_archive_is_moved_away(
    admin, tmp_path: Path
) -> None:
    """The whole point of freezing the revision: the render path never reads a file.

    Design 3.7 says a machine that only has ``data/vocab.db`` produces a byte-for-byte
    identical source block. The archive is moved out of its temporary directory here and
    the two responses are compared exactly.
    """
    root = tmp_path / "sources"
    root.mkdir()
    token = "esbarchive"
    ids = _import_two_words(admin, root, token=token, lexicon_name="esb-archive")
    state_id = ids["states"][f"word{token}"]

    before = _detail(admin, state_id)
    assert _field(before["sources"], "word")["selected"][0]["source_revision_url"]

    moved = tmp_path / "archive-gone"
    shutil.move(str(root), str(moved))
    assert not root.exists()

    after = _detail(admin, state_id)
    assert after == before, "the block must not depend on the source file still existing"
