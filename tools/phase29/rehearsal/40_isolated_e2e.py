"""One-shot Phase 2.9 rehearsal against an explicitly disposable SQLite file.

Run from backend with: python ../tools/phase29/rehearsal/40_isolated_e2e.py PATH_TO_NEW_DB
The frozen inputs are read only. The new database and derived subset live beside PATH_TO_NEW_DB.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import make_engine
from app.models import (
    EntrySourceEvidence,
    Lexicon,
    LexiconEntry,
    SourceArtifact,
    SourceWikitextLine,
    User,
)
from app.services.concise_meaning import (
    ConciseMeaningCitationProposal,
    ConciseMeaningProposal,
    ConciseMeaningRefused,
    confirm,
    entry_short_meanings,
    propose,
)
from app.services.pinned_wikitext_writer import write_pinned_lines
from app.services.public_lexicon_confirm import confirm_plan
from app.services.public_lexicon_plan import build_plan
from app.services.userdata import get_or_create_word_state

ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research/rehearsal"
SOURCES = FROZEN / "sources"
ARCHIVE = FROZEN / "pilot/zh-pinned-wikitext-300.json"
NAME = "kaoyan-2027-public-rehearsal"


def line_hash(source: str, oldid: str, page_hash: str, number: int, raw: str) -> str:
    return hashlib.sha256("\n".join((source, oldid, page_hash, str(number), raw)).encode()).hexdigest()


def local_manifest(source: Path, destination: Path) -> Path:
    """Adapt the frozen delivery manifest to current plan metadata, for this run only."""
    manifest = json.loads(source.read_text(encoding="utf-8"))
    for spec in manifest["sources"]:
        spec["provenance"] = {
            "publisher": spec["id"], "version": "frozen-delivery",
            "obtained_at_utc": "2026-09-27T00:00:00Z", "license_id": "UNVERIFIED",
            "use_scope": "isolated-rehearsal", "display_scope": "isolated-rehearsal",
            "storage_locator": str(SOURCES / spec["file"]),
        }
        if spec["id"] == "zhwiktionary-pinned-oldid":
            spec["revision"] = {
                "column": "zh_oldid",
                "url_template": "https://zh.wiktionary.org/w/index.php?oldid={revision}",
            }
    destination.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return destination


def main(db: Path) -> None:
    db = db.resolve()
    if db.exists() or db.parent.resolve() == (ROOT / "data").resolve():
        raise SystemExit("Refusing existing database or application data directory")
    db.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, VOCAB_DATA_DIR=str(db.parent),
               VOCAB_REAL_DATA_DIR=str(db.parent), PYTHONIOENCODING="utf-8")
    command = [sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"),
               "-x", f"db_url=sqlite:///{db.as_posix()}", "upgrade", "head"]
    subprocess.run(command, cwd=ROOT / "backend", env=env, check=True)
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    demo_root = db.parent / "demo20-sources"
    (demo_root / "demo20").mkdir(parents=True)
    for name in ("netem-words.csv", "zhwiktionary.csv", "wikdict.csv"):
        shutil.copyfile(SOURCES / "demo20" / name, demo_root / "demo20" / name)
    shutil.copyfile(SOURCES / "decisions-demo20.json", demo_root / "decisions-demo20.json")
    demo_manifest = local_manifest(SOURCES / "manifest-demo20.json",
                                   demo_root / "manifest.json")
    with Session(engine) as session:
        admin = User(username="phase29-isolated-reviewer", role="admin", is_active=True)
        lexicon = Lexicon(name=NAME, visibility="public", source_type="phase29-isolated")
        session.add_all((admin, lexicon))
        session.commit()
        demo = build_plan(manifest_path=demo_manifest,
                          source_root=demo_root,
                          decisions_path=demo_root / "decisions-demo20.json",
                          target_lexicon=NAME)
        first = confirm_plan(session, plan=demo, administrator=admin, source_root=demo_root)

    # A three-word slice from the same preserved delivery files; the source CSVs keep
    # their original physical line numbers. No full-list decision is generated.
    local = db.parent / "phase29-sources"
    local.mkdir(exist_ok=False)
    for name, source in (("wikdict.csv", SOURCES / "wikdict.csv"),
                         ("zhwiktionary-v4en.csv", SOURCES / "v4/zhwiktionary-v4en.csv")):
        shutil.copyfile(source, local / name)
    with (SOURCES / "netem-words.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        selected = [row for row in reader if row["word"] in ("prior", "performance", "fertiliser")]
        with (local / "netem-words.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(selected)
    local_manifest(SOURCES / "manifest-r4-v4en.json", local / "manifest.json")
    manifest = json.loads((local / "manifest.json").read_text(encoding="utf-8"))
    manifest["required_fields"] = []
    for source in manifest["sources"]:
        if source["id"] == "zhwiktionary-pinned-oldid":
            source["file"] = "zhwiktionary-v4en.csv"
    (local / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    decisions = {"format_version": 1, "decisions": [
        {"action": "select", "normalized_word": word, "field": "meaning",
         "evidence": [{"source_id": "zhwiktionary-pinned-oldid", "line": row}],
         "note": "仅隔离预演：按 20 词试裁定记录选固定来源汇总行"}
        for word, row in (("performance", 36), ("prior", 139))
    ]}
    (local / "decisions.json").write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
    plan = build_plan(manifest_path=local / "manifest.json", source_root=local,
                      decisions_path=local / "decisions.json", target_lexicon=NAME)
    if not plan["confirmation_ready"]:
        raise RuntimeError(f"three-word plan blocked: {plan['confirmation_blockers']}")
    with Session(engine) as session:
        admin = session.scalar(select(User).where(User.username == "phase29-isolated-reviewer"))
        second = confirm_plan(session, plan=plan, administrator=admin, source_root=local)
        artifact_bytes = ARCHIVE.read_bytes()
        artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
        artifact = SourceArtifact(role="meaning", name=ARCHIVE.name, publisher="preserved pinned pages",
                                  version="frozen", obtained_at_utc="2026-09-27T00:00:00Z",
                                  format="json", mapping_json="{}",
                                  mapping_sha256=hashlib.sha256(b"{}").hexdigest(),
                                  file_sha256=artifact_hash, byte_size=len(artifact_bytes),
                                  license_id="unverified", license_text_sha256="0" * 64,
                                  use_scope="isolated rehearsal", display_scope="isolated rehearsal",
                                  storage_locator=str(ARCHIVE))
        session.add(artifact)
        session.commit()
        artifact_id = artifact.id
    pages = json.loads(artifact_bytes)
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for oldid, numbers in (("9576029", (12, 15, 16, 19, 23)),
                               ("8457333", (3, 10, 12)),
                               ("7831922", (3, 6, 17, 20))):
            page = pages[oldid]
            digest = hashlib.sha256(page.encode()).hexdigest()
            lines = page.split("\n")
            write_pinned_lines(connection, artifact_id=artifact_id, artifact_path=ARCHIVE,
                               expected_file_sha256=artifact_hash,
                               source_id="zhwiktionary-pinned-oldid", oldid=oldid,
                               expected_page_sha256=digest,
                               expected_line_sha256={n: line_hash("zhwiktionary-pinned-oldid", oldid,
                                                                  digest, n, lines[n - 1]) for n in numbers})
    with Session(engine) as session:
        admin = session.scalar(select(User).where(User.username == "phase29-isolated-reviewer"))
        entries = {entry.normalized_word: entry for entry in session.scalars(select(LexiconEntry))}
        def evidence(word: str, oldid: str) -> int:
            return session.scalar(select(EntrySourceEvidence.id).where(
                EntrySourceEvidence.lexicon_entry_id == entries[word].id,
                EntrySourceEvidence.source_revision == oldid,
                EntrySourceEvidence.field_kind == "meaning"))
        def line(oldid: str, number: int) -> int:
            return session.scalar(select(SourceWikitextLine.id).where(
                SourceWikitextLine.page_revision == oldid,
                SourceWikitextLine.line_number == number))
        prior = entries["prior"]
        prior_rows = propose(session, entry=prior, actor=admin, proposals=[
            ConciseMeaningProposal(text=text, provenance_kind="derived", derivation_note=note,
                display_order=order, source_locator=f"zhwiktionary:9576029:{number}",
                source_evidence_id=evidence("prior", "9576029"),
                primary_wikitext_line_id=line("9576029", number),
                pos_key=pos, pos_label=label, pos_order=pos_order,
                pos_source="pos_section", pos_evidence_locator=f"zhwiktionary:9576029:{heading}",
                pos_wikitext_line_id=line("9576029", heading), language="en")
            for text, note, order, number, pos, label, pos_order, heading in (
                ("先前的", "由先的、前的改写", 1, 15, "adj", "形容词", 1, 12),
                ("更重要的", "由原文单独抽义", 2, 16, "adj", "形容词", 1, 12),
                ("事先", "由事先、先、预先抽义", 1, 23, "adv", "副词", 2, 19),
            )])
        performance = entries["performance"]
        perf_rows = propose(session, entry=performance, actor=admin, proposals=[
            ConciseMeaningProposal(text=text, provenance_kind="derived", derivation_note=note,
                display_order=order, source_locator=f"zhwiktionary:8457333:{number}",
                source_evidence_id=evidence("performance", "8457333"),
                primary_wikitext_line_id=line("8457333", number),
                pos_key="noun", pos_label="名词", pos_order=1,
                pos_source="reviewer", pos_evidence_locator="zhwiktionary:8457333:10",
                pos_wikitext_line_id=line("8457333", 10), language="en",
                citations=(ConciseMeaningCitationProposal(
                    citation_locator="zhwiktionary:8457333:10",
                    source_evidence_id=evidence("performance", "8457333"),
                    wikitext_line_id=line("8457333", 10)),) if number == 12 else ())
            for text, note, order, number in (("表演", "从原文抽义；名词为人工试判", 1, 10),
                                             ("执行", "从同一原文抽义；名词为人工试判", 2, 10),
                                             ("性能", "从软件领域原文抽义；名词为人工试判", 3, 12))])
        for row in prior_rows + perf_rows:
            confirm(session, meaning=row, confirmer=admin, note="隔离库试裁定记录逐行核对")
        fertiliser = entries["fertiliser"]
        with_source_refused = False
        try:
            propose(session, entry=fertiliser, actor=admin, proposals=[
                ConciseMeaningProposal(text="肥料", provenance_kind="source", display_order=1,
                    source_locator="zhwiktionary:7831922:20", primary_wikitext_line_id=line("7831922", 20),
                    pos_key="noun", pos_source="reviewer",
                    pos_evidence_locator="zhwiktionary:7831922:20", pos_wikitext_line_id=line("7831922", 20),
                    language="en")])
        except ConciseMeaningRefused:
            with_source_refused = True
        assert with_source_refused
        session.commit()
        readback = {word: entry_short_meanings(session, [entries[word].id]).get(entries[word].id, [])
                    for word in ("prior", "performance", "fertiliser")}
        assert [[m["text"] for m in g["meanings"]] for g in readback["prior"]] == [["先前的", "更重要的"], ["事先"]]
        assert [[m["text"] for m in g["meanings"]] for g in readback["performance"]] == [["表演", "执行", "性能"]]
        assert readback["fertiliser"] == []
        state_ids = {}
        for word in ("prior", "performance", "fertiliser"):
            state_ids[word] = get_or_create_word_state(session, admin, entries[word]).id
        session.commit()
        admin_id = admin.id
        counts = {table: session.scalar(select(func.count()).select_from(model)) for table, model in (
            ("entries", LexiconEntry), ("csv_evidence", EntrySourceEvidence),
            ("wikitext_lines", SourceWikitextLine))}
    from app.api.deps import get_current_user
    from app.db import get_session
    from app.main import app

    def isolated_session():
        with Session(engine) as current:
            yield current

    app.dependency_overrides[get_session] = isolated_session
    app.dependency_overrides[get_current_user] = lambda: User(
        id=admin_id, username="phase29-isolated-reviewer", role="admin", is_active=True)
    try:
        client = TestClient(app)
        api = {}
        for word, state_id in state_ids.items():
            response = client.get(f"/api/words/state/{state_id}")
            assert response.status_code == 200, response.text
            api[word] = response.json()["concise_meanings"]
        assert [[m["text"] for m in g["meanings"]] for g in api["prior"]] == [["先前的", "更重要的"], ["事先"]]
        assert [[m["text"] for m in g["meanings"]] for g in api["performance"]] == [["表演", "执行", "性能"]]
        assert api["fertiliser"] == []
    finally:
        app.dependency_overrides.clear()
    result = {"first_import": first, "second_import": second,
              "counts": counts, "api_readback": api,
              "fertiliser_source_refused": with_source_refused,
              "key_imported": "key" in entries}
    (db.parent / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: 40_isolated_e2e.py NEW_DB_PATH")
    main(Path(sys.argv[1]))
