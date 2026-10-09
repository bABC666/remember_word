"""The first 1000 NETEM adjudications remain tied to preserved source positions."""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/phase29/top1000.py"


def _module():
    spec = importlib.util.spec_from_file_location("top1000", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_first_occurrence_and_frozen_overlap_are_preserved():
    from sys import path

    path.insert(0, str(ROOT / "tools/phase29"))
    from frozen300 import resolve as frozen_resolve

    records = _module().resolve(ROOT)
    frozen = {record["word"]: record for record in frozen_resolve(ROOT)}
    assert len(records) == 1000
    assert len({record["word"] for record in records}) == 1000
    assert [record["word"] for record in records[:5]] == ["the", "be", "a", "to", "of"]
    assert records[-1]["word"] == "grant"
    assert sum(record["word"] in frozen for record in records) == 56
    for record in records:
        if record["word"] in frozen:
            assert record["groups"] == frozen[record["word"]]["groups"]


def test_new_decisions_have_bounded_groups_and_honest_wikdict_positions():
    records = _module().resolve(ROOT)
    for record in records:
        if record.get("reused_frozen"):
            continue
        for group in record["groups"]:
            assert 1 <= len(group["meanings"]) <= 3
            for sense in group["meanings"]:
                assert sense["basis"] == "W"
                assert sense["language"] == "en"
                assert sense["source_locator"].startswith("wikdict:")
                assert sense["raw_line"]
                assert sense["pos_source"] == "reviewer"
                assert sense["text"] in sense["raw_line"] or sense["derivation_note"]
                assert sense["original_positions"]
                assert all(item["body_sha256"] and item["dict_byte_length"] > 0
                           for item in sense["original_positions"])
    key = next(row for row in records if row["word"] == "key")
    assert [s["text"] for g in key["groups"] for s in g["meanings"]] == ["钥匙"]
    assert key["groups"][0]["meanings"][0]["original_positions"][0]["index"] == 13333
    for word, pos in (("mean", "noun"), ("show", "verb"), ("off", "adv"),
                      ("set", "verb"), ("care", "noun"), ("ago", "adv")):
        row = next(item for item in records if item["word"] == word)
        sense = next(s for g in row["groups"] if g["pos_key"] == pos
                     for s in g["meanings"])
        assert sense["decision"] == "pending"


def test_wikdict_grammar_conflicts_have_explicit_rulings():
    with (ROOT / "tools/phase29/top1000-pos-audit.tsv").open(
        encoding="utf-8", newline=""
    ) as stream:
        audit = list(csv.DictReader(stream, delimiter="|"))
    assert len(audit) == 38
    assert sum(row["ruling"] == "candidate" for row in audit) == 6
    records = {row["word"]: row for row in _module().resolve(ROOT)}
    for row in audit:
        sense = next(s for group in records[row["word"]]["groups"]
                     if group["pos_key"] == row["pos"]
                     for s in group["meanings"] if s["text"] == row["text"])
        assert sense["decision"] == ("pending" if row["ruling"] == "candidate"
                                     else "propose")


def test_isolated_run_confirms_key_only_with_its_noun_original():
    script = ROOT / "tools/phase29/run_top1000.py"
    spec = importlib.util.spec_from_file_location("run_top1000", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = ROOT / "test-artifacts" / f"phase29-top1000-pytest-{uuid4().hex[:12]}"
    report = module.run(ROOT, output, words=["key", "the", "state", "device"])
    assert report["selected_words"] == 4
    assert report["by_word"]["key"]["confirmed"] == 1
    assert report["by_word"]["device"]["confirmed"] == 0
    with sqlite3.connect(output / "vocab.db") as connection:
        key = connection.execute(
            "select m.text,m.pos_key,m.pos_source from entry_concise_meaning m "
            "join lexicon_entry e on e.id=m.lexicon_entry_id "
            "where e.normalized_word='key' and m.status='confirmed'"
        ).fetchall()
        assert key == [("钥匙", "noun", "reviewer")]
        assert connection.execute(
            "select count(*) from entry_concise_meaning m "
            "join lexicon_entry e on e.id=m.lexicon_entry_id "
            "where e.sequence>1000 and m.status='confirmed'"
        ).fetchone()[0] == 0
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db import make_engine
    from app.models import EntryConciseMeaning, EntrySourceEvidence, LexiconEntry, SourceArtifact
    from app.services.concise_meaning import ConciseMeaningRefused, _require_confirmable_evidence

    engine = make_engine(f"sqlite:///{(output / 'vocab.db').as_posix()}")
    with Session(engine) as session:
        entry = session.scalar(select(LexiconEntry).where(LexiconEntry.normalized_word == "key"))
        meaning = session.scalar(select(EntryConciseMeaning).where(
            EntryConciseMeaning.lexicon_entry_id == entry.id,
            EntryConciseMeaning.status == "confirmed"))
        evidence = session.get(EntrySourceEvidence, meaning.source_evidence_id)
        artifact = session.get(SourceArtifact, evidence.source_artifact_id)
        _require_confirmable_evidence(session, entry, meaning)
        artifact.file_sha256 = "0" * 64
        session.flush()
        with pytest.raises(ConciseMeaningRefused, match="持久证据"):
            _require_confirmable_evidence(session, entry, meaning)
        session.rollback()
