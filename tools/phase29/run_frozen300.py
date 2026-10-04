"""Replay frozen Phase 2.9 proposals into a fresh, isolated SQLite database.

Only local preserved files are read. The output directory must be new and is never
inferred from application settings. The actor is explicitly named ``phase29-ai``;
confirmation means the evidence gate passed in this rehearsal, not human approval or
permission to use the source in production.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from frozen300 import EVIDENCE, key_evidence, resolve

from app.db import make_engine
from app.models import (
    EntrySourceEvidence,
    Lexicon,
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
from app.services.pinned_wikitext_writer import _paths, write_pinned_lines
from app.services.public_lexicon_plan import evidence_idempotency_key

LABEL = {"noun": "名词", "verb": "动词", "adj": "形容词", "adv": "副词",
         "pron": "代词", "det": "限定词", "prep": "介词", "conj": "连词"}
SOURCE_ID = "zhwiktionary-pinned-oldid"


def _configured_pytest_temp_root(root: Path) -> Path | None:
    """Allow pytest's relocated base only during a test, within the system temp tree."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        options = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    except ValueError:
        return None
    raw = None
    for index, option in enumerate(options):
        if option.startswith("--basetemp="):
            raw = option.partition("=")[2]
        elif option == "--basetemp" and index + 1 < len(options):
            raw = options[index + 1]
    if not raw:
        return None
    configured = Path(raw)
    if not configured.is_absolute():
        configured = root / "backend" / configured
    configured = configured.resolve()
    system_temp = Path(tempfile.gettempdir()).resolve()
    if configured == system_temp or not configured.is_relative_to(system_temp):
        return None
    return configured


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(path: Path) -> list[tuple[int, dict]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(enumerate(csv.DictReader(source), 2))


def _artifact(session: Session, path: Path, *, mapping: str, name: str,
              version: str, format: str) -> SourceArtifact:
    data = path.read_bytes()
    row = SourceArtifact(
        role="meaning", name=name, publisher="local frozen evidence",
        version=version, obtained_at_utc="2026-09-26T00:00:00Z", format=format,
        mapping_json=mapping, mapping_sha256=_sha(mapping.encode()),
        file_sha256=_sha(data), byte_size=len(data), license_id="UNVERIFIED",
        license_text_sha256="", use_scope="isolated adjudication only",
        display_scope="isolated adjudication only", storage_locator=str(path),
    )
    session.add(row)
    session.flush()
    return row


def _heading(page: str, number: int, pos: str) -> int | None:
    facts = _paths(page.split("\n"))
    language, path, _, _ = facts[number - 1]
    if language not in ("英語", "英语"):
        return None
    for index in range(number - 2, -1, -1):
        _language, parent, key, title = facts[index]
        if key == pos and (path == f"{parent} > {title}"
                               or path.startswith(f"{parent} > {title} > ")):
            return index + 1
    return None


def _migrate(root: Path, db: Path) -> None:
    env = dict(os.environ, VOCAB_DATA_DIR=str(db.parent),
               VOCAB_REAL_DATA_DIR=str(db.parent), PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(root / "backend/alembic.ini"),
         "-x", f"db_url=sqlite:///{db.as_posix()}", "upgrade", "head"],
        cwd=root / "backend", env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise RuntimeError(f"isolated migration failed:\n{result.stdout}\n{result.stderr}")


def run(root: Path, output: Path, *, words: list[str] | None = None) -> dict:
    root, output = root.resolve(), output.resolve()
    archive_root = (root / EVIDENCE).resolve()
    allowed = [root / "test-artifacts", root / ".pytest-tmp",
               root / "backend/.pytest-tmp"]
    pytest_temp = _configured_pytest_temp_root(root)
    if pytest_temp is not None:
        allowed.append(pytest_temp)
    if (not archive_root.is_dir() or
            not any(output.is_relative_to(directory) for directory in allowed) or
            (words is None and not output.name.startswith("phase29-frozen300-"))):
        raise ValueError("isolated output must be a new phase29-frozen300 directory "
                         "under test-artifacts (or pytest's temporary root)")
    db = output / "vocab.db"
    if db.exists() or (output / "report.json").exists():
        raise FileExistsError("isolated run already exists; choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    records = resolve(root)
    if words is not None:
        wanted = set(words)
        records = [record for record in records if record["word"] in wanted]
        if len(records) != len(wanted):
            raise ValueError("requested test words are not in the frozen 300")
    _migrate(root, db)

    zh_path = archive_root / "sources/v4/zhwiktionary-v4en.csv"
    wik_path = archive_root / "sources/wikdict.csv"
    pages_path = archive_root / "pilot/zh-pinned-wikitext-300.json"
    pages = json.loads(pages_path.read_text(encoding="utf-8"))
    zh_rows = {row["word"].casefold(): (line, row) for line, row in _rows(zh_path)}
    wik_rows = {row["word"].casefold(): (line, row) for line, row in _rows(wik_path)}
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    with Session(engine) as session:
        actor = User(username="phase29-ai", display_name="AI 释义裁定", role="admin",
                     is_active=True)
        lexicon = Lexicon(name="frozen-300-isolated", visibility="public",
                          source_type="phase29-frozen", entry_count=len(records))
        session.add_all((actor, lexicon))
        session.flush()
        entries = {}
        for index, record in enumerate(records, 1):
            entry = LexiconEntry(lexicon_id=lexicon.id, word=record["word"],
                                 normalized_word=record["word"], sequence=index)
            session.add(entry)
            session.flush()
            entries[record["word"]] = entry

        zh_art = _artifact(session, zh_path, name="zhwiktionary-v4en.csv",
                           version="frozen oldid per row", format="delimited-text-v1",
                           mapping='{"columns":{"word":"word","meaning":"zh_meaning"}}')
        wik_art = _artifact(session, wik_path, name="wikdict.csv",
                            version="2026-06-23 frozen CSV", format="delimited-text-v1",
                            mapping='{"columns":{"word":"word","meaning":"wikdict_meaning"}}')
        pages_art = _artifact(session, pages_path, name=pages_path.name,
                              version="300 pinned oldids", format="json", mapping="{}")
        run_row = PublicImportRun(plan_sha256=_sha((str(output) + "phase29").encode()),
                                  run_id="phase29-frozen300-isolated",
                                  target_lexicon_id=lexicon.id,
                                  confirmed_by_user_id=actor.id,
                                  confirmed_by_username=actor.username,
                                  status="applied")
        session.add(run_row)
        session.flush()
        for artifact in (zh_art, wik_art, pages_art):
            session.add(PublicImportRunSource(import_run_id=run_row.id,
                                              source_artifact_id=artifact.id,
                                              outcome="created"))
        session.flush()
        evidence_ids = {}
        for record in records:
            word, oldid, entry = record["word"], record["oldid"], entries[record["word"]]
            for basis, found, artifact, field, revision in (
                ("Z", zh_rows.get(word), zh_art, "zh_meaning", oldid),
                ("W", wik_rows.get(word), wik_art, "wikdict_meaning", ""),
            ):
                if not found or not found[1][field].strip():
                    continue
                line, source_row = found
                if basis == "Z" and source_row.get("zh_oldid") != oldid:
                    continue
                raw = source_row[field]
                evidence = EntrySourceEvidence(
                    lexicon_entry_id=entry.id, source_artifact_id=artifact.id,
                    import_run_id=run_row.id, normalized_word=word,
                    row_locator=line, field_kind="meaning", sense_key=f"meaning@{line}",
                    raw_word=source_row["word"], raw_text=raw,
                    evidence_sha256=evidence_idempotency_key(
                        file_sha256=artifact.file_sha256,
                        mapping_sha256=artifact.mapping_sha256,
                        line=line, field="meaning", raw_value=raw),
                    decision="selected", selected_for_default=False,
                    confirmed_by_username=actor.username, source_revision=revision,
                )
                session.add(evidence)
                session.flush()
                evidence_ids[(word, basis)] = evidence.id
        session.flush()
        lexicon_id, actor_id = lexicon.id, actor.id
        pages_art_id, pages_art_hash = pages_art.id, pages_art.file_sha256
        entry_ids = {word: entry.id for word, entry in entries.items()}
        session.commit()

    # Copy only cited English wikitext lines and their genuine POS headings. The
    # writer re-reads the preserved archive and checks file/page/line fingerprints.
    line_ids = {}
    line_errors = {}
    with sqlite3.connect(db) as conn:
        for record in records:
            oldid = record["oldid"]
            page = pages[oldid]
            numbers = set()
            for group in record["groups"]:
                for sense in group["meanings"]:
                    if sense["basis"] != "Z" or not sense["source_locator"]:
                        continue
                    number = sense["line_number"]
                    numbers.add(number)
                    heading = _heading(page, number, group["pos_key"])
                    if heading:
                        numbers.add(heading)
            if not numbers:
                continue
            page_hash = _sha(page.encode("utf-8"))
            raw_lines = page.split("\n")
            hashes = {number: source_wikitext_line_sha256(
                source_id=SOURCE_ID, page_revision=oldid,
                page_text_sha256=page_hash, line_number=number,
                raw_text=raw_lines[number - 1]) for number in numbers}
            try:
                write_pinned_lines(
                    conn, artifact_id=pages_art_id, artifact_path=pages_path,
                    expected_file_sha256=pages_art_hash,
                    source_id=SOURCE_ID, oldid=oldid, expected_page_sha256=page_hash,
                    expected_line_sha256=hashes,
                )
            except ValueError as error:
                line_errors[record["word"]] = str(error)
                continue
            for number in numbers:
                row = conn.execute(
                    "select id from source_wikitext_line where source_id=? "
                    "and page_revision=? and line_number=?",
                    (SOURCE_ID, oldid, number),
                ).fetchone()
                line_ids[(oldid, number)] = row[0]
        conn.commit()

    by_word = {}
    with Session(engine) as session:
        actor = session.get(User, actor_id)
        lexicon = session.get(Lexicon, lexicon_id)
        for record in records:
            word = record["word"]
            entry = session.get(LexiconEntry, entry_ids[word])
            proposals = []
            senses = []
            for group in record["groups"]:
                for sense in group["meanings"]:
                    basis, oldid = sense["basis"], record["oldid"]
                    if basis == "AI":
                        proposal = ConciseMeaningProposal(
                            text=sense["text"], provenance_kind="ai_supplement",
                            display_order=1, derivation_note=sense["reason"],
                            pos_key="", pos_source="none", pos_order=len(record["groups"]) + 1,
                            language="",
                        )
                    else:
                        locator = sense["source_locator"]
                        line_number = sense.get("line_number")
                        evidence_id = evidence_ids.get((word, basis))
                        line_id = (line_ids.get((oldid, line_number))
                                   if basis == "Z" and evidence_id else None)
                        heading = (_heading(pages[oldid], line_number, group["pos_key"])
                                   if basis == "Z" and line_number else None)
                        heading_id = line_ids.get((oldid, heading)) if heading and evidence_id else None
                        pos_source = "pos_section" if heading_id and line_id else "reviewer"
                        pos_locator = (f"zhwiktionary:{oldid}:{heading}" if pos_source == "pos_section"
                                       else locator)
                        kind = ("source" if evidence_id and sense["text"] in sense["raw_line"]
                                else "derived")
                        proposal = ConciseMeaningProposal(
                            text=sense["text"], provenance_kind=kind,
                            display_order=sense["display_order"],
                            source_locator=locator, derivation_note=(
                                "" if kind == "source" else
                                "由原文抽义、繁简转换或按语境改写；" + sense["reason"]),
                            source_evidence_id=evidence_id,
                            primary_wikitext_line_id=line_id,
                            pos_wikitext_line_id=heading_id if pos_source == "pos_section" else line_id,
                            pos_key=group["pos_key"], pos_label=LABEL.get(group["pos_key"], group["pos_key"]),
                            pos_order=group["pos_order"], pos_source=pos_source,
                            pos_evidence_locator=pos_locator, language="en",
                        )
                    proposals.append(proposal)
                    senses.append(sense)
            result = {"attempted": len(proposals), "proposed": 0,
                      "confirmed": 0, "pending": 0, "errors": []}
            # Propose one at a time so a doubtful sense cannot block sound ones.
            for sense, proposal in zip(senses, proposals):
                try:
                    with session.begin_nested():
                        row = propose(session, entry=entry, proposals=[proposal], actor=actor)[0]
                    result["proposed"] += 1
                    sense["proposal_id"] = row.id
                    if sense["decision"] == "pending":
                        result["pending"] += 1
                        continue
                    try:
                        with session.begin_nested():
                            confirm(session, meaning=row, confirmer=actor,
                                    note="AI 语义裁定；隔离库证据链核实")
                        result["confirmed"] += 1
                        sense["decision"] = "confirmed"
                    except ConciseMeaningRefused as error:
                        sense["decision"] = "pending"
                        sense["gate_reason"] = str(error)
                        result["pending"] += 1
                except (ConciseMeaningRefused, ValueError) as error:
                    sense["decision"] = "pending"
                    sense["gate_reason"] = str(error)
                    result["pending"] += 1
                    result["errors"].append(str(error))
            if word in line_errors:
                result["errors"].append("逐行写入：" + line_errors[word])
            session.commit()
            by_word[word] = result

    report = {
        "database": str(db), "actor": "phase29-ai", "source_license": "UNVERIFIED",
        "attempted_words": len(records), "attempted_senses": sum(v["attempted"] for v in by_word.values()),
        "proposed_senses": sum(v["proposed"] for v in by_word.values()),
        "confirmed_words": sum(v["confirmed"] > 0 for v in by_word.values()),
        "confirmed_senses": sum(v["confirmed"] for v in by_word.values()),
        "pending_words": sum(v["pending"] > 0 for v in by_word.values()),
        "pending_senses": sum(v["pending"] for v in by_word.values()),
        "by_word": by_word, "line_write_errors": line_errors,
        "basis_counts": dict(Counter(s["basis"] for r in records for g in r["groups"]
                                     for s in g["meanings"])),
    }
    (output / "word-records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "key-evidence.json").write_text(
        json.dumps(key_evidence(root), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.output), ensure_ascii=False, indent=2))
