"""0013 preserves legacy citations while adding three independent line bindings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.models import EntryConciseMeaning, EntryConciseMeaningCitation
from tests.test_concise_meaning_pos import (
    check_names,
    columns,
    connect,
    foreign_keys,
    index_names,
    insert_citation,
    insert_meaning,
    integrity,
    migrated,
    seed_evidence,
    seed_lexicon_and_entry,
    step,
)
from tests.test_pinned_wikitext_writer import seed as seed_archive
from tests.test_pinned_wikitext_writer import write as write_page

REV_0012 = "0012_source_wikitext_line"
REV_0013 = "0013_concise_meaning_wikitext_binding"


def line_id(db: Path, oldid: str, number: int) -> int:
    with connect(db) as conn:
        return conn.execute(
            "select id from source_wikitext_line where page_revision=? and line_number=?",
            (oldid, number),
        ).fetchone()[0]


def test_prior_performance_and_wikdict_keep_distinct_bindings(tmp_path: Path) -> None:
    db = migrated(tmp_path, REV_0012)
    lexicon, prior_entry = seed_lexicon_and_entry(db)
    with connect(db) as conn:
        conn.execute(
            "insert into lexicon_entry (lexicon_id,word,normalized_word,phonetic,"
            "part_of_speech,source_meanings,source_raw,default_anchor,semantic_note,"
            "possible_issue,frequency_source,created_at,updated_at) values "
            "(?, 'performance', 'performance', '', '', '[]', '', '', '', 0, '', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')", (lexicon,),
        )
        performance_entry = conn.execute("select last_insert_rowid()").fetchone()[0]
    prior = insert_meaning(db, prior_entry, pos_key="adj", pos_source="pos_section",
                           pos_evidence="zhwiktionary:9576029:12")
    first = insert_meaning(db, performance_entry, text="表演", pos_key="noun",
                           pos_source="reviewer", pos_evidence="zhwiktionary:8457333:10")
    second = insert_meaning(db, performance_entry, order=2, text="执行", pos_key="noun",
                            pos_source="reviewer", pos_evidence="zhwiktionary:8457333:10")
    csv_id = seed_evidence(db, prior_entry, locator=37, text="表演", tag="wikdict")
    citation = insert_citation(db, prior, order=1, locator="wikdict:37", evidence_id=csv_id)
    line_citation = insert_citation(db, prior, order=2,
                                    locator="zhwiktionary:9576029:16")
    with connect(db) as conn:
        conn.execute(
            "insert into entry_concise_meaning_revision (lexicon_entry_id,normalized_word,"
            "concise_meaning_id,action,display_order,text,created_at) "
            "values (?, 'prior', ?, 'propose', 1, '先前的', '2026-01-01 00:00:00')",
            (prior_entry, prior),
        )
    artifact = seed_archive(db)
    write_page(db, artifact, "9576029", (12, 15, 16))
    write_page(db, artifact, "8457333", (10,))
    before_checks = {table: check_names(db, table) for table in
                     ("entry_concise_meaning", "entry_concise_meaning_citation")}
    before_fks = {table: foreign_keys(db, table) for table in before_checks}
    before_indexes = {table: index_names(db, table) for table in before_checks}

    result = step(tmp_path, db, "upgrade", REV_0013)
    assert result.returncode == 0, result.stdout + result.stderr
    with connect(db) as conn:
        assert tuple(conn.execute("select primary_wikitext_line_id,pos_wikitext_line_id "
                          "from entry_concise_meaning where id=?", (prior,)).fetchone()) == (None, None)
        assert tuple(conn.execute("select wikitext_line_id,source_evidence_id "
                          "from entry_concise_meaning_citation where id=?", (citation,)).fetchone()) == (None, csv_id)
        conn.execute("update entry_concise_meaning set primary_wikitext_line_id=?, "
                     "pos_wikitext_line_id=? where id=?",
                     (line_id(db, "9576029", 15), line_id(db, "9576029", 12), prior))
        shared = line_id(db, "8457333", 10)
        conn.execute("update entry_concise_meaning set primary_wikitext_line_id=?, "
                     "pos_wikitext_line_id=? where id in (?,?)", (shared, shared, first, second))
        conn.execute("update entry_concise_meaning_citation set wikitext_line_id=? "
                     "where id=?", (line_id(db, "9576029", 16), line_citation))
    with connect(db) as conn:
        assert tuple(conn.execute("select primary_wikitext_line_id,pos_wikitext_line_id "
                                  "from entry_concise_meaning where id=?", (prior,)).fetchone()) == (
                                      line_id(db, "9576029", 15), line_id(db, "9576029", 12))
        assert [row[0] for row in conn.execute("select primary_wikitext_line_id "
                "from entry_concise_meaning where id in (?,?) order by id", (first, second))] == [shared, shared]
        assert tuple(conn.execute("select source_evidence_id,wikitext_line_id "
                                  "from entry_concise_meaning_citation where id=?", (citation,)).fetchone()) == (csv_id, None)
        assert tuple(conn.execute("select source_evidence_id,wikitext_line_id "
                                  "from entry_concise_meaning_citation where id=?", (line_citation,)).fetchone()) == (
                                      None, line_id(db, "9576029", 16))
        assert tuple(conn.execute("select action,text from entry_concise_meaning_revision "
                                  "where concise_meaning_id=?", (prior,)).fetchone()) == ("propose", "先前的")
    for table, checks in before_checks.items():
        assert check_names(db, table) == checks
        assert before_fks[table].items() <= foreign_keys(db, table).items()
        assert before_indexes[table] <= index_names(db, table)
    assert integrity(db) == ("ok", [])


def test_model_declares_the_same_independent_foreign_keys(tmp_path: Path) -> None:
    db = migrated(tmp_path)
    for model, table, names in (
        (EntryConciseMeaning, "entry_concise_meaning",
         ("primary_wikitext_line_id", "pos_wikitext_line_id")),
        (EntryConciseMeaningCitation, "entry_concise_meaning_citation",
         ("wikitext_line_id",)),
    ):
        physical = foreign_keys(db, table)
        for name in names:
            column = model.__table__.columns[name]
            assert column.nullable
            assert {fk.target_fullname for fk in column.foreign_keys} == {
                "source_wikitext_line.id"}
            assert physical[name] == ("source_wikitext_line", "RESTRICT")
            assert name in columns(db, table)


def test_each_binding_is_a_real_foreign_key(tmp_path: Path) -> None:
    db = migrated(tmp_path)
    _lexicon, entry = seed_lexicon_and_entry(db)
    meaning = insert_meaning(db, entry)
    citation = insert_citation(db, meaning)
    for table, row_id, column in (
        ("entry_concise_meaning", meaning, "primary_wikitext_line_id"),
        ("entry_concise_meaning", meaning, "pos_wikitext_line_id"),
        ("entry_concise_meaning_citation", citation, "wikitext_line_id"),
    ):
        with connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"update {table} set {column}=999999 where id=?", (row_id,))
        assert foreign_keys(db, table)[column] == ("source_wikitext_line", "RESTRICT")


def test_new_columns_do_not_give_supplements_a_source_or_undetermined_pos_a_basis(
    tmp_path: Path,
) -> None:
    db = migrated(tmp_path)
    _lexicon, entry = seed_lexicon_and_entry(db)
    supplement = insert_meaning(db, entry, kind="ai_supplement", locator="",
                                note="test supplement")
    ordinary = insert_meaning(db, entry, order=2)
    artifact = seed_archive(db)
    write_page(db, artifact, "9576029", (12, 15))
    with connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="supplement has no primary source"):
            conn.execute("update entry_concise_meaning set primary_wikitext_line_id=? "
                         "where id=?", (line_id(db, "9576029", 15), supplement))
        with pytest.raises(sqlite3.IntegrityError, match="undetermined POS has no line basis"):
            conn.execute("update entry_concise_meaning set pos_wikitext_line_id=? "
                         "where id=?", (line_id(db, "9576029", 12), ordinary))


def test_lossy_downgrade_refuses_before_ddl_but_unbound_history_survives(tmp_path: Path) -> None:
    db = migrated(tmp_path)
    _lexicon, entry = seed_lexicon_and_entry(db)
    meaning = insert_meaning(db, entry)
    citation = insert_citation(db, meaning)
    artifact = seed_archive(db)
    write_page(db, artifact, "9576029", (12, 15))
    with connect(db) as conn:
        conn.execute("update entry_concise_meaning_citation set wikitext_line_id=? where id=?",
                     (line_id(db, "9576029", 15), citation))
    result = step(tmp_path, db, "downgrade", REV_0012)
    assert result.returncode != 0
    assert "绑定" in result.stdout + result.stderr
    assert "wikitext_line_id" in columns(db, "entry_concise_meaning_citation")
    with connect(db) as conn:
        assert conn.execute("select version_num from alembic_version").fetchone()[0] == REV_0013
        conn.execute("update entry_concise_meaning_citation set wikitext_line_id=null where id=?", (citation,))
    result = step(tmp_path, db, "downgrade", REV_0012)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "wikitext_line_id" not in columns(db, "entry_concise_meaning_citation")
    with connect(db) as conn:
        assert tuple(conn.execute("select citation_locator,source_evidence_id "
                                  "from entry_concise_meaning_citation where id=?", (citation,)).fetchone()) == ("wikdict:37", None)
    assert integrity(db) == ("ok", [])


def test_pinned_line_proposal_confirms_only_with_matching_english_heading(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session

    from app.db import make_engine
    from app.models import Lexicon, LexiconEntry, User
    from app.services.concise_meaning import (
        ConciseMeaningProposal,
        ConciseMeaningRefused,
        confirm,
        entry_short_meanings,
        propose,
    )
    from tests.test_concise_meaning import _persist_csv_evidence

    db = migrated(tmp_path)
    archive_id = seed_archive(db)
    write_page(db, archive_id, "9576029", (12, 15, 16, 19, 23))
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    with Session(engine) as session:
        admin = User(username="reviewer", role="admin", is_active=True)
        lexicon = Lexicon(name="isolated", visibility="public", source_type="test")
        session.add_all((admin, lexicon))
        session.flush()
        entry = LexiconEntry(lexicon_id=lexicon.id, word="prior", normalized_word="prior")
        session.add(entry)
        session.commit()
        evidence_id = _persist_csv_evidence(
            session, lexicon, entry, row=2, raw="先的；更重要的；事先",
            source="zhwiktionary-v4en.csv", revision="9576029",
        )
        gloss = line_id(db, "9576029", 15)
        heading = line_id(db, "9576029", 12)
        row = propose(session, entry=entry, actor=admin, proposals=[ConciseMeaningProposal(
            text="先前的", provenance_kind="derived", derivation_note="由先的改写",
            display_order=1, source_locator="zhwiktionary:9576029:15",
            source_evidence_id=evidence_id, primary_wikitext_line_id=gloss,
            pos_key="adj", pos_source="pos_section",
            pos_evidence_locator="zhwiktionary:9576029:12",
            pos_wikitext_line_id=heading, language="en",
        )])[0]
        confirm(session, meaning=row, confirmer=admin)
        assert entry_short_meanings(session, [entry.id])[entry.id][0]["meanings"][0]["text"] == "先前的"
        with pytest.raises(ConciseMeaningRefused):
            propose(session, entry=entry, actor=admin, proposals=[ConciseMeaningProposal(
                text="更重要的", provenance_kind="derived", derivation_note="抽义",
                display_order=2, source_locator="zhwiktionary:9576029:16",
                source_evidence_id=evidence_id, primary_wikitext_line_id=gloss,
                pos_key="adj", pos_source="pos_section",
                pos_evidence_locator="zhwiktionary:9576029:12",
                pos_wikitext_line_id=heading, language="en",
            )])


def test_proposal_file_keeps_all_three_line_bindings(tmp_path: Path) -> None:
    import json

    from app.cli import load_proposal_file

    file = tmp_path / "proposal.json"
    file.write_text(json.dumps({
        "format_version": 2, "lexicon": "isolated", "entries": [{
            "word": "prior", "pos_groups": [{
                "pos_key": "adj", "pos_source": "pos_section",
                "pos_evidence_locator": "zhwiktionary:9576029:12",
                "pos_wikitext_line_id": 1, "language": "en", "meanings": [{
                    "text": "先前的", "provenance_kind": "derived",
                    "source_locator": "zhwiktionary:9576029:15",
                    "primary_wikitext_line_id": 2, "derivation_note": "改写",
                    "citations": [{"citation_locator": "zhwiktionary:9576029:16",
                                   "wikitext_line_id": 3}],
                }],
            }],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    proposal = load_proposal_file(file, lexicon_name="isolated")[0]["proposals"][0]
    assert proposal.primary_wikitext_line_id == 2
    assert proposal.pos_wikitext_line_id == 1
    assert proposal.citations[0].wikitext_line_id == 3
