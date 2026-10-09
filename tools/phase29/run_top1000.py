"""Replay reviewed top-1000 decisions in a fresh full-import isolated database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from frozen300 import EVIDENCE, key_evidence
from run_frozen300 import LABEL, SOURCE_ID, _artifact, _heading
from top1000 import resolve

from app.db import make_engine
from app.models import (
    EntrySourceEvidence,
    LexiconEntry,
    PublicImportRun,
    PublicImportRunSource,
    SourceArtifact,
    User,
    source_wikitext_line_sha256,
)
from app.services.concise_meaning import (
    ConciseMeaningProposal,
    ConciseMeaningRefused,
    confirm,
    propose,
)
from app.services.pinned_wikitext_writer import write_pinned_lines

FULL_SCRIPT = ROOT / "tools/phase29/rehearsal/50_full_lexicon_rehearsal.py"
FULL_WIKDICT_SHA = "49b06b69461056a653c05540349819f6cea15a1a5d7fd8c63c8ea161b0e969bf"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(root: Path, output: Path, *, words: list[str] | None = None) -> dict:
    root, output = root.resolve(), output.resolve()
    allowed = (root / "test-artifacts").resolve()
    if (not (root / EVIDENCE).is_dir() or not output.is_relative_to(allowed)
            or not output.name.startswith("phase29-top1000-") or
            (words is None and output.name.startswith("phase29-top1000-pytest-"))):
        raise ValueError("new phase29-top1000 isolated output under test-artifacts required")
    db = output / "vocab.db"
    if output.exists():
        raise FileExistsError("output already exists; choose a new directory")
    records = resolve(root)
    selected = records if words is None else [r for r in records if r["word"] in set(words)]
    if words is not None and len(selected) != len(set(words)):
        raise ValueError("test words must belong to the first 1000 unique NETEM words")
    output.mkdir(parents=True)
    env = dict(os.environ, VOCAB_DATA_DIR=str(output), VOCAB_REAL_DATA_DIR=str(output),
               PYTHONIOENCODING="utf-8")
    process = subprocess.run(
        [sys.executable, str(FULL_SCRIPT), str(db), "--full-wikdict",
         "--skip-representative-meanings"], cwd=root / "backend", env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    (output / "full-import.log").write_text(process.stdout + process.stderr, encoding="utf-8")
    if process.returncode:
        raise RuntimeError(f"full isolated import failed; see {output / 'full-import.log'}")
    full_result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    if (full_result["unique_membership"] != 5528 or not full_result["order_matches"]
            or full_result["integrity_check"] != "ok" or
            full_result["representative"] != {"skipped": True}):
        raise RuntimeError("full import order, integrity, or scope drift")
    package = output / "full-lexicon-input"
    wik_path = package / "wikdict.csv"
    if _sha(wik_path.read_bytes()) != FULL_WIKDICT_SHA:
        raise RuntimeError("full WikDict export fingerprint drift")
    pinned_path = root / EVIDENCE / "pilot/zh-pinned-wikitext-300.json"
    pages = json.loads(pinned_path.read_text(encoding="utf-8"))
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    with Session(engine) as session:
        actor = User(username="phase29-ai-top1000", display_name="AI 释义裁定",
                     role="admin", is_active=True)
        session.add(actor)
        run_row = session.scalar(select(PublicImportRun).order_by(PublicImportRun.id.desc()))
        lexicon_id = run_row.target_lexicon_id
        entries = {e.normalized_word: e.id for e in session.scalars(
            select(LexiconEntry).where(LexiconEntry.lexicon_id == lexicon_id,
                                       LexiconEntry.sequence <= 1000))}
        wik_art = session.scalar(select(SourceArtifact).where(SourceArtifact.name == "wikdict.csv"))
        if wik_art.file_sha256 != FULL_WIKDICT_SHA or len(entries) != 1000:
            raise RuntimeError("imported WikDict artifact or first-1000 membership drift")
        pinned_art = _artifact(session, pinned_path, mapping="{}",
                               name=pinned_path.name, version="300 pinned oldids", format="json")
        session.add(PublicImportRunSource(import_run_id=run_row.id,
                                          source_artifact_id=pinned_art.id,
                                          outcome="created"))
        evidence = {}
        for row in session.scalars(select(EntrySourceEvidence).where(
            EntrySourceEvidence.lexicon_entry_id.in_(list(entries.values())),
            EntrySourceEvidence.field_kind == "meaning")):
            artifact = session.get(SourceArtifact, row.source_artifact_id)
            basis = "W" if artifact.name == "wikdict.csv" else "Z"
            evidence[(row.normalized_word, basis)] = {
                "id": row.id, "row_locator": row.row_locator, "raw_text": row.raw_text,
            }
        actor_id, pinned_id, pinned_hash = actor.id, pinned_art.id, pinned_art.file_sha256
        session.commit()

    # Translate frozen-300 WikDict locators to the deterministic full export row.
    # Wording and POS remain unchanged; only the citation's CSV version changes.
    wik_evidence = {key[0]: value for key, value in evidence.items() if key[1] == "W"}
    for record in selected:
        if not record.get("reused_frozen"):
            continue
        row = wik_evidence.get(record["word"])
        for group in record["groups"]:
            for sense in group["meanings"]:
                if sense["basis"] != "W":
                    continue
                if row is None or sense["text"] not in row["raw_text"] and not any(
                        marker in row["raw_text"] for marker in (sense.get("source_text", ""),
                                                              sense.get("raw_line", "")) if marker):
                    sense["decision"] = "pending"
                    sense["reason"] += "；全量 WikDict 证据行未能承接冻结裁定"
                    sense["source_locator"] = ""
                    continue
                sense.update(source_locator=f"wikdict:{row['row_locator']}",
                             raw_line=row["raw_text"], source_text=row["raw_text"],
                             line_number=row["row_locator"])

    line_ids = {}
    line_errors = {}
    with sqlite3.connect(db) as connection:
        for record in selected:
            if not record.get("reused_frozen"):
                continue
            oldid = record["oldid"]
            page = pages[oldid]
            raw_lines = page.split("\n")
            numbers = set()
            for group in record["groups"]:
                for sense in group["meanings"]:
                    if sense["basis"] == "Z" and sense["source_locator"]:
                        n = sense["line_number"]
                        numbers.add(n)
                        h = _heading(page, n, group["pos_key"])
                        if h:
                            numbers.add(h)
            if not numbers:
                continue
            page_hash = _sha(page.encode("utf-8"))
            hashes = {number: source_wikitext_line_sha256(
                source_id=SOURCE_ID, page_revision=oldid, page_text_sha256=page_hash,
                line_number=number, raw_text=raw_lines[number - 1]) for number in numbers}
            try:
                write_pinned_lines(connection, artifact_id=pinned_id,
                                   artifact_path=pinned_path, expected_file_sha256=pinned_hash,
                                   source_id=SOURCE_ID, oldid=oldid,
                                   expected_page_sha256=page_hash,
                                   expected_line_sha256=hashes)
            except ValueError as error:
                line_errors[record["word"]] = str(error)
                continue
            for number in numbers:
                line_ids[(oldid, number)] = connection.execute(
                    "select id from source_wikitext_line where source_id=? "
                    "and page_revision=? and line_number=?",
                    (SOURCE_ID, oldid, number)).fetchone()[0]
        connection.commit()

    by_word = {}
    with Session(engine) as session:
        actor = session.get(User, actor_id)
        for record in selected:
            word = record["word"]
            entry = session.get(LexiconEntry, entries[word])
            outcome = {"attempted": 0, "proposed": 0, "confirmed": 0,
                       "pending": 0, "errors": []}
            for group in record["groups"]:
                for sense in group["meanings"]:
                    outcome["attempted"] += 1
                    basis = sense["basis"]
                    locator = sense["source_locator"]
                    source_row = evidence.get((word, basis)) if basis != "AI" else None
                    if basis == "AI" or not locator:
                        proposal = ConciseMeaningProposal(
                            text=sense["text"], provenance_kind="ai_supplement",
                            display_order=1, derivation_note=sense["reason"],
                            pos_key="", pos_source="none", language="")
                    else:
                        oldid = record.get("oldid", "")
                        n = sense.get("line_number")
                        line_id = line_ids.get((oldid, n)) if basis == "Z" else None
                        heading = (_heading(pages[oldid], n, group["pos_key"])
                                   if basis == "Z" and n else None)
                        heading_id = line_ids.get((oldid, heading)) if heading else None
                        pos_source = "pos_section" if line_id and heading_id else "reviewer"
                        pos_locator = (f"zhwiktionary:{oldid}:{heading}" if pos_source == "pos_section"
                                       else locator)
                        kind = "source" if sense["text"] in sense["raw_line"] else "derived"
                        proposal = ConciseMeaningProposal(
                            text=sense["text"], provenance_kind=kind,
                            display_order=sense["display_order"], source_locator=locator,
                            derivation_note=("" if kind == "source" else
                                             "由固定中文原文抽义或繁简转换；" + sense["reason"]),
                            source_evidence_id=source_row["id"] if source_row else None,
                            primary_wikitext_line_id=line_id,
                            pos_wikitext_line_id=(heading_id if pos_source == "pos_section"
                                                  else line_id),
                            pos_key=group["pos_key"],
                            pos_label=LABEL.get(group["pos_key"], group["pos_key"]),
                            pos_order=group["pos_order"], pos_source=pos_source,
                            pos_evidence_locator=pos_locator, language="en")
                    try:
                        with session.begin_nested():
                            meaning = propose(session, entry=entry,
                                              proposals=[proposal], actor=actor)[0]
                        outcome["proposed"] += 1
                        sense["proposal_id"] = meaning.id
                        if sense["decision"] == "pending":
                            outcome["pending"] += 1
                            continue
                        try:
                            with session.begin_nested():
                                confirm(session, meaning=meaning, confirmer=actor,
                                        note="AI 语义裁定；隔离库持久证据链核实")
                            sense["decision"] = "confirmed"
                            outcome["confirmed"] += 1
                        except ConciseMeaningRefused as error:
                            sense["decision"] = "pending"
                            sense["gate_reason"] = str(error)
                            outcome["pending"] += 1
                    except (ConciseMeaningRefused, ValueError) as error:
                        sense["decision"] = "pending"
                        sense["gate_reason"] = str(error)
                        outcome["pending"] += 1
                        outcome["errors"].append(str(error))
            if word in line_errors:
                outcome["errors"].append("逐行写入：" + line_errors[word])
            session.commit()
            by_word[word] = outcome

    with sqlite3.connect(db) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        beyond = connection.execute(
            "select count(*) from entry_concise_meaning m join lexicon_entry e "
            "on e.id=m.lexicon_entry_id where e.sequence>1000 and m.status='confirmed'"
        ).fetchone()[0]
        statuses = dict(connection.execute(
            "select status,count(*) from entry_concise_meaning group by status").fetchall())
    if integrity != "ok" or foreign_keys or beyond:
        raise RuntimeError("isolated DB integrity or rank scope check failed")
    report = {
        "database": str(db), "selected_words": len(selected),
        "frozen_reused": sum(bool(r.get("reused_frozen")) for r in selected),
        "confirmed_words": sum(v["confirmed"] > 0 for v in by_word.values()),
        "confirmed_senses": sum(v["confirmed"] for v in by_word.values()),
        "candidate_words": sum(v["pending"] > 0 for v in by_word.values()),
        "candidate_senses": sum(v["pending"] for v in by_word.values()),
        "no_source_words": sum(bool(r.get("no_source")) for r in selected),
        "without_confirmed_words": sum(v["confirmed"] == 0 for v in by_word.values()),
        "basis_counts": dict(Counter(s["basis"] for r in selected
                                     for g in r["groups"] for s in g["meanings"])),
        "by_word": by_word, "line_write_errors": line_errors,
        "sqlite_statuses": statuses, "integrity_check": integrity,
        "foreign_key_violations": len(foreign_keys),
        "confirmed_beyond_1000": beyond,
        "full_import": {key: full_result[key] for key in
                        ("unique_membership", "order_matches", "integrity_check",
                         "frozen_300_evidence_exact")},
    }
    (output / "word-records.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "key-evidence.json").write_text(
        json.dumps(key_evidence(root), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(ROOT, args.output), ensure_ascii=False, indent=2))
