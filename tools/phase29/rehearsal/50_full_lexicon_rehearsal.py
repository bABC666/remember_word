"""Import preserved NETEM membership and source fields into a new isolated DB.

Run from backend::

    ./.venv/Scripts/python.exe ../tools/phase29/rehearsal/50_full_lexicon_rehearsal.py ../test-artifacts/phase29-full-e2e-01/vocab.db
    ./.venv/Scripts/python.exe ../tools/phase29/rehearsal/50_full_lexicon_rehearsal.py ../test-artifacts/phase29-full-wikdict-e2e-NEW/vocab.db --full-wikdict

This is a local import rehearsal. A source-field choice is not a POS meaning verdict.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import make_engine
from app.models import (
    EntryConciseMeaning,
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
    propose,
)
from app.services.pinned_wikitext_writer import write_pinned_lines
from app.services.public_lexicon_confirm import confirm_plan
from app.services.public_lexicon_plan import build_plan
from app.services.userdata import get_or_create_word_state

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research"
RAW = EVIDENCE / "raw/netem_full_list.json"
FROZEN_PLAN = EVIDENCE / "rehearsal/out/plan-r4-v4en.json"
FROZEN_SOURCES = EVIDENCE / "rehearsal/sources"
ARCHIVE = EVIDENCE / "rehearsal/pilot/zh-pinned-wikitext-300.json"
LEXICON_NAME = "kaoyan-5530-isolated-rehearsal"
RAW_SHA256 = "6d71a301321056291902bc4804e223c6926dca0d629a45076a6ca5adab185f62"
PLAN_SHA256 = "15f8459b51826cadf998e727be8133e37b5838718889faf8d816af417678e69f"
ZH = "zhwiktionary-pinned-oldid"
WIKDICT = "wikdict-en-zh-2026-06-23"
WIKDICT_ZIP = EVIDENCE / "raw/wikdict-en-zh.zip"
WIKDICT_ZIP_SHA256 = "62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830"
WIKDICT_HEADER = ["word", "wikdict_meaning", "wikdict_headword", "wikdict_entry_index",
                  "wikdict_entry_offset", "match_rule", "wikdict_package_sha256"]
HAN = re.compile(r"[\u4e00-\u9fff]")
GLOSS_DIV = re.compile(r"<div>([^<]*)</div>")


def _pinned_json(path: Path, expected_sha256: str) -> dict:
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"preserved input fingerprint changed: {path} {actual}")
    return json.loads(raw)


def membership_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep first normalized spelling in source rank order; never read its gloss."""
    membership: list[dict] = []
    duplicates: list[dict] = []
    first: dict[str, tuple[str, int]] = {}
    for expected_rank, row in enumerate(rows, 1):
        word, rank = row["单词"], row["序号"]
        if type(rank) is not int or rank != expected_rank or not isinstance(word, str):
            raise ValueError(f"invalid source rank or word at row {expected_rank}")
        normalized = word.strip().casefold()
        if not normalized:
            raise ValueError(f"empty word at source rank {rank}")
        if normalized in first:
            kept_word, kept_rank = first[normalized]
            duplicates.append({"word": word, "netem_rank": rank,
                               "kept_word": kept_word, "kept_rank": kept_rank})
            continue
        first[normalized] = (word, rank)
        membership.append({"word": word, "netem_rank": rank,
                           "freq_claimed_mixed_exam_corpus": row["词频"]})
    return membership, duplicates


def _wikdict_glosses(body: str) -> list[str]:
    glosses: list[str] = []
    for raw in GLOSS_DIV.findall(body):
        if HAN.search(raw):
            gloss = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
            if gloss and gloss not in glosses:
                glosses.append(gloss)
    return glosses


def wikdict_backfill_rows(
    archive: Path, membership: list[dict], expected_sha256: str = WIKDICT_ZIP_SHA256
) -> tuple[list[dict], list[str]]:
    """Read pinned StarDict idx/dict; retain zero-based idx and decompressed dict offset."""
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"preserved WikDict ZIP fingerprint changed: {digest}")
    with zipfile.ZipFile(archive) as z:
        index = z.read("wikdict-en-zh/stardict.idx")
        dictionary = z.read("wikdict-en-zh/stardict.dict")
    by_exact: dict[str, list[tuple[str, int, int, list[str]]]] = {}
    cursor = 0
    entry_index = 0
    while cursor < len(index):
        end = index.find(b"\0", cursor)
        if end < 0 or end + 9 > len(index):
            raise ValueError(f"malformed StarDict index at byte {cursor}")
        headword = index[cursor:end].decode("utf-8")
        offset = int.from_bytes(index[end + 1:end + 5], "big")
        size = int.from_bytes(index[end + 5:end + 9], "big")
        if offset + size > len(dictionary):
            raise ValueError(f"StarDict entry outside dictionary: {entry_index}")
        glosses = _wikdict_glosses(dictionary[offset:offset + size].decode("utf-8"))
        if glosses:
            item = (headword, entry_index, offset, glosses)
            by_exact.setdefault(headword.strip().casefold(), []).append(item)
        cursor = end + 9
        entry_index += 1
    rows: list[dict] = []
    uncovered: list[str] = []
    for member in membership:
        word = member["word"]
        entries = by_exact.get(word.strip().casefold(), [])
        if not entries:
            uncovered.append(word)
            continue
        glosses = list(dict.fromkeys(gloss for _headword, _index, _offset, found in entries
                                     for gloss in found))
        rows.append({
            "word": word, "wikdict_meaning": "；".join(glosses),
            "wikdict_headword": "|".join(item[0] for item in entries),
            "wikdict_entry_index": "|".join(str(item[1]) for item in entries),
            "wikdict_entry_offset": "|".join(str(item[2]) for item in entries),
            "match_rule": "casefold_exact",
            "wikdict_package_sha256": digest,
        })
    return rows, uncovered


def select_backfill_meaning_fields(
    frozen_entries: list[dict], wikdict_lines: dict[str, int], words: list[str]
) -> list[dict]:
    """Select raw fields only; WikDict cannot establish a POS-specific meaning."""
    frozen = {entry["normalized_word"]: entry for entry in frozen_entries}
    decisions: list[dict] = []
    for word in words:
        zh = [item for item in frozen.get(word, {}).get("evidence", {}).get("meaning", [])
              if item["source_id"] == ZH and item["raw_value"].strip()]
        if len(zh) > 1:
            decisions.append({"action": "no_default", "normalized_word": word,
                              "field": "meaning", "note": "多个 zh.wiktionary 位置待人工复核"})
            continue
        selected = ({"source_id": ZH, "line": zh[0]["line"]} if zh else
                    {"source_id": WIKDICT, "line": wikdict_lines[word]}
                    if word in wikdict_lines else None)
        if selected:
            decisions.append({
                "action": "select", "normalized_word": word, "field": "meaning",
                "evidence": [selected],
                "note": "隔离导入原始字段；不构成按词性的短释义裁定",
            })
    return decisions


def wikdict_original_entries(archive: Path, indices: set[int]) -> list[dict]:
    """Expose the exact pinned bodies for a small position audit (currently key)."""
    with zipfile.ZipFile(archive) as z:
        index = z.read("wikdict-en-zh/stardict.idx")
        dictionary = z.read("wikdict-en-zh/stardict.dict")
    found: list[dict] = []
    cursor = 0
    number = 0
    while cursor < len(index):
        end = index.index(b"\0", cursor)
        offset = int.from_bytes(index[end + 1:end + 5], "big")
        size = int.from_bytes(index[end + 5:end + 9], "big")
        if number in indices:
            body = dictionary[offset:offset + size]
            found.append({
                "entry_index_zero_based": number,
                "headword": index[cursor:end].decode("utf-8"),
                "dict_offset": offset, "dict_size": size,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "body_html": body.decode("utf-8"),
            })
        cursor = end + 9
        number += 1
    if {item["entry_index_zero_based"] for item in found} != indices:
        raise ValueError("requested StarDict original entry missing")
    return found


def select_frozen_meaning_fields(entries: list[dict]) -> tuple[list[dict], dict, list[dict]]:
    """Choose a raw default by source priority; keep other values in plan evidence."""
    decisions: list[dict] = []
    coverage = {"both": 0, "zh_only": 0, "wikdict_only": 0, "neither": 0}
    unresolved: list[dict] = []
    for entry in entries:
        word = entry["normalized_word"]
        evidence = entry["evidence"]["meaning"]
        zh = [item for item in evidence if item["source_id"] == ZH and item["raw_value"].strip()]
        wikdict = [item for item in evidence if item["source_id"] == WIKDICT and item["raw_value"].strip()]
        coverage[("both" if zh and wikdict else "zh_only" if zh else
                  "wikdict_only" if wikdict else "neither")] += 1
        if len(zh) > 1 or len(wikdict) > 1:
            unresolved.append({"word": word, "reason": "multiple_positions_in_one_source",
                               "positions": [{"source_id": item["source_id"], "line": item["line"]}
                                             for item in evidence]})
            decisions.append({"action": "no_default", "normalized_word": word,
                              "field": "meaning", "note": "同一来源有多个原文位置，隔离预演不猜选"})
            continue
        chosen = (zh or wikdict)
        if not chosen:
            unresolved.append({"word": word, "reason": "no_preserved_meaning"})
            continue
        decisions.append({
            "action": "select", "normalized_word": word, "field": "meaning",
            "evidence": [{"source_id": chosen[0]["source_id"], "line": chosen[0]["line"]}],
            "note": "隔离导入的原始字段选择：zh.wiktionary 优先，缺项用 WikDict；非按词性释义裁定",
        })
    return decisions, coverage, unresolved


def _manifest(package: Path, *, full_wikdict: bool = False) -> Path:
    manifest = json.loads((FROZEN_SOURCES / "manifest-r4-v4en.json").read_text(encoding="utf-8"))
    manifest["required_fields"] = []  # full membership also includes words outside the 300-source slice
    for spec in manifest["sources"]:
        original = spec["file"]
        spec["file"] = {"netem-words.csv": "netem-words.csv",
                        "wikdict.csv": "wikdict.csv",
                        "v4/zhwiktionary-v4en.csv": "zhwiktionary-v4en.csv"}[original]
        spec["provenance"] = {
            "publisher": spec["id"], "version": "frozen-delivery",
            "obtained_at_utc": "2026-09-27T00:00:00Z", "license_id": "UNVERIFIED",
            "use_scope": "isolated-rehearsal", "display_scope": "isolated-rehearsal",
            "storage_locator": str(FROZEN_SOURCES / original),
        }
        if full_wikdict and spec["id"] == WIKDICT:
            spec["provenance"]["storage_locator"] = str(WIKDICT_ZIP)
        if spec["id"] == ZH:
            spec["revision"] = {
                "column": "zh_oldid",
                "url_template": "https://zh.wiktionary.org/w/index.php?oldid={revision}",
            }
    path = package / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _copy_frozen_sources(package: Path, frozen_plan: dict, *, full_wikdict: bool = False) -> None:
    sources = {source["source_id"]: source for source in frozen_plan["sources"]}
    for source_id, source_file, target in (
        (ZH, FROZEN_SOURCES / "v4/zhwiktionary-v4en.csv", package / "zhwiktionary-v4en.csv"),
        (WIKDICT, FROZEN_SOURCES / "wikdict.csv", package / "wikdict.csv"),
    ):
        if full_wikdict and source_id == WIKDICT:
            continue
        digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
        if digest != sources[source_id]["file"]["sha256"]:
            raise ValueError(f"frozen source drift: {source_id}")
        shutil.copyfile(source_file, target)


def _check_representative_api(engine, db: Path) -> dict:
    """Recheck the six already trialled short senses on the full imported lexicon."""
    artifact_bytes = ARCHIVE.read_bytes()
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    pages = json.loads(artifact_bytes)
    with Session(engine) as session:
        artifact = SourceArtifact(
            role="meaning", name=ARCHIVE.name, publisher="preserved pinned pages",
            version="frozen", obtained_at_utc="2026-09-27T00:00:00Z", format="json",
            mapping_json="{}", mapping_sha256=hashlib.sha256(b"{}").hexdigest(),
            file_sha256=artifact_hash, byte_size=len(artifact_bytes),
            license_id="UNVERIFIED", license_text_sha256="0" * 64,
            use_scope="isolated-rehearsal", display_scope="isolated-rehearsal",
            storage_locator=str(ARCHIVE),
        )
        session.add(artifact)
        session.commit()
        artifact_id = artifact.id
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for oldid, numbers in (("9576029", (12, 15, 16, 19, 23)),
                               ("8457333", (3, 10, 12)),
                               ("7831922", (3, 6, 17, 20))):
            page = pages[oldid]
            page_hash = hashlib.sha256(page.encode()).hexdigest()
            lines = page.split("\n")
            hashes = {number: hashlib.sha256("\n".join((ZH, oldid, page_hash,
                       str(number), lines[number - 1])).encode()).hexdigest()
                      for number in numbers}
            write_pinned_lines(
                connection, artifact_id=artifact_id, artifact_path=ARCHIVE,
                expected_file_sha256=artifact_hash, source_id=ZH, oldid=oldid,
                expected_page_sha256=page_hash, expected_line_sha256=hashes,
            )
    with Session(engine) as session:
        admin = session.scalar(select(User).where(User.username == "phase29-full-isolated-reviewer"))
        entries = {entry.normalized_word: entry for entry in session.scalars(select(LexiconEntry).where(
            LexiconEntry.normalized_word.in_(("prior", "performance", "fertiliser", "key"))))}

        def evidence(word: str, oldid: str) -> int:
            result = session.scalar(select(EntrySourceEvidence.id).where(
                EntrySourceEvidence.lexicon_entry_id == entries[word].id,
                EntrySourceEvidence.source_revision == oldid,
                EntrySourceEvidence.field_kind == "meaning"))
            if result is None:
                raise RuntimeError(f"missing pinned CSV evidence for {word}")
            return result

        def line(oldid: str, number: int) -> int:
            return session.scalar(select(SourceWikitextLine.id).where(
                SourceWikitextLine.page_revision == oldid,
                SourceWikitextLine.line_number == number))

        prior_rows = propose(session, entry=entries["prior"], actor=admin, proposals=[
            ConciseMeaningProposal(
                text=text, provenance_kind="derived", derivation_note=note,
                display_order=order, source_locator=f"zhwiktionary:9576029:{number}",
                source_evidence_id=evidence("prior", "9576029"),
                primary_wikitext_line_id=line("9576029", number),
                pos_key=pos, pos_label=label, pos_order=pos_order,
                pos_source="pos_section",
                pos_evidence_locator=f"zhwiktionary:9576029:{heading}",
                pos_wikitext_line_id=line("9576029", heading), language="en",
            ) for text, note, order, number, pos, label, pos_order, heading in (
                ("先前的", "由先的、前的改写", 1, 15, "adj", "形容词", 1, 12),
                ("更重要的", "由原文单独抽义", 2, 16, "adj", "形容词", 1, 12),
                ("事先", "由事先、先、预先抽义", 1, 23, "adv", "副词", 2, 19),
            )])
        performance_rows = propose(session, entry=entries["performance"], actor=admin,
            proposals=[ConciseMeaningProposal(
                text=text, provenance_kind="derived", derivation_note=note,
                display_order=order, source_locator=f"zhwiktionary:8457333:{number}",
                source_evidence_id=evidence("performance", "8457333"),
                primary_wikitext_line_id=line("8457333", number),
                pos_key="noun", pos_label="名词", pos_order=1,
                pos_source="reviewer", pos_evidence_locator="zhwiktionary:8457333:10",
                pos_wikitext_line_id=line("8457333", 10), language="en",
                citations=(ConciseMeaningCitationProposal(
                    citation_locator="zhwiktionary:8457333:10",
                    source_evidence_id=evidence("performance", "8457333"),
                    wikitext_line_id=line("8457333", 10)),) if number == 12 else (),
            ) for text, note, order, number in (
                ("表演", "从原文抽义；名词为人工试判", 1, 10),
                ("执行", "从同一原文抽义；名词为人工试判", 2, 10),
                ("性能", "从软件领域原文抽义；名词为人工试判", 3, 12),
            )])
        for row in prior_rows + performance_rows:
            confirm(session, meaning=row, confirmer=admin, note="完整词表隔离库逐行回归")
        refusal = ""
        try:
            propose(session, entry=entries["fertiliser"], actor=admin, proposals=[
                ConciseMeaningProposal(
                    text="肥料", provenance_kind="source", display_order=1,
                    source_locator="zhwiktionary:7831922:20",
                    primary_wikitext_line_id=line("7831922", 20),
                    pos_key="noun", pos_source="reviewer",
                    pos_evidence_locator="zhwiktionary:7831922:20",
                    pos_wikitext_line_id=line("7831922", 20), language="en",
                )])
        except ConciseMeaningRefused as error:
            refusal = str(error)
        if not refusal:
            raise RuntimeError("fertiliser source claim was accepted")
        state_ids = {word: get_or_create_word_state(session, admin, entries[word]).id
                     for word in ("prior", "performance", "fertiliser")}
        session.commit()
        admin_id = admin.id
        key_id = entries["key"].id
    from app.api.deps import get_current_user
    from app.db import get_session
    from app.main import app

    def isolated_session():
        with Session(engine) as current:
            yield current

    app.dependency_overrides[get_session] = isolated_session
    app.dependency_overrides[get_current_user] = lambda: User(
        id=admin_id, username="phase29-full-isolated-reviewer", role="admin", is_active=True)
    try:
        client = TestClient(app)
        api = {}
        for word, state_id in state_ids.items():
            response = client.get(f"/api/words/state/{state_id}")
            if response.status_code != 200:
                raise RuntimeError(f"API read failed for {word}: {response.status_code}")
            api[word] = response.json()["concise_meanings"]
    finally:
        app.dependency_overrides.clear()
    grouped = {word: [(group["pos_key"], [item["text"] for item in group["meanings"]])
                      for group in api[word]] for word in api}
    if (grouped["prior"] != [("adj", ["先前的", "更重要的"]), ("adv", ["事先"])]
            or grouped["performance"] != [("noun", ["表演", "执行", "性能"])]
            or grouped["fertiliser"] != []):
        raise RuntimeError(f"representative API mismatch: {grouped}")
    if "肥料" in json.dumps(api["fertiliser"], ensure_ascii=False):
        raise RuntimeError("unsupported fertiliser meaning reached the API")
    with Session(engine) as session:
        key_confirmed = session.scalar(select(func.count()).select_from(EntryConciseMeaning).where(
            EntryConciseMeaning.lexicon_entry_id == key_id,
            EntryConciseMeaning.status == "confirmed"))
    if key_confirmed:
        raise RuntimeError("key gained an unsupported confirmed meaning")
    return {"api_groups": grouped, "fertiliser_source_refusal": refusal,
            "key_short_meaning_confirmed": bool(key_confirmed)}


def main(db: Path, *, full_wikdict: bool = False,
         skip_representative_meanings: bool = False) -> None:
    db = db.resolve()
    allowed = (ROOT / "test-artifacts").resolve()
    if not db.is_relative_to(allowed) or db.exists() or db.suffix != ".db":
        raise SystemExit("target must be a new .db under test-artifacts")
    db.parent.mkdir(parents=True, exist_ok=True)
    package = db.parent / "full-lexicon-input"
    package.mkdir(exist_ok=False)
    raw = _pinned_json(RAW, RAW_SHA256)
    frozen_plan = _pinned_json(FROZEN_PLAN, PLAN_SHA256)
    original_rows = raw["5530考研词汇词频排序表"]
    membership, duplicates = membership_rows(original_rows)
    with (package / "netem-words.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(membership[0]))
        writer.writeheader()
        writer.writerows(membership)
    _copy_frozen_sources(package, frozen_plan, full_wikdict=full_wikdict)
    wikdict_rows: list[dict] = []
    uncovered: list[str] = []
    if full_wikdict:
        wikdict_rows, uncovered = wikdict_backfill_rows(WIKDICT_ZIP, membership)
        with (package / "wikdict.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=WIKDICT_HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(wikdict_rows)
        (package / "wikdict-uncovered.json").write_text(
            json.dumps(uncovered, ensure_ascii=False, indent=2), encoding="utf-8")
        wikdict_lines = {row["word"].strip().casefold(): line
                         for line, row in enumerate(wikdict_rows, 2)}
        decisions = select_backfill_meaning_fields(
            frozen_plan["entries"], wikdict_lines,
            [row["word"].strip().casefold() for row in membership])
        coverage = {}
        unresolved = []
    else:
        decisions, coverage, unresolved = select_frozen_meaning_fields(frozen_plan["entries"])
    manifest = _manifest(package, full_wikdict=full_wikdict)
    decisions_path = package / "decisions.json"
    decisions_path.write_text(json.dumps({"format_version": 1, "decisions": decisions},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    plan = build_plan(manifest_path=manifest, source_root=package,
                      decisions_path=decisions_path, target_lexicon=LEXICON_NAME)
    if not plan["confirmation_ready"]:
        raise RuntimeError(f"full plan blocked: {plan['confirmation_blockers'][:12]}")
    expected_words = [row["word"].strip().casefold() for row in membership]
    planned_words = [entry["normalized_word"] for entry in plan["entries"]]
    if planned_words != expected_words or len(plan["entries"]) != len(membership):
        raise RuntimeError("planned membership or order differs from first source occurrence")
    frozen_words = {entry["normalized_word"] for entry in frozen_plan["entries"]}
    planned_300 = [entry for entry in plan["entries"] if entry["normalized_word"] in frozen_words]
    if len(planned_300) != 300:
        raise RuntimeError(f"frozen 300 membership drift: {len(planned_300)}")
    priority_300 = {"both": 0, "zh_only": 0, "wikdict_only": 0,
                    "neither": 0, "selected_zh": 0, "selected_wikdict": 0}
    if full_wikdict:
        for entry in planned_300:
            zh = [item for item in entry["evidence"]["meaning"]
                  if item["source_id"] == ZH and item["raw_value"].strip()]
            wik = [item for item in entry["evidence"]["meaning"]
                   if item["source_id"] == WIKDICT and item["raw_value"].strip()]
            priority_300["both" if zh and wik else "zh_only" if zh else
                         "wikdict_only" if wik else "neither"] += 1
            selected = entry["default_evidence"]["meaning"]
            expected = zh or wik
            if len(selected) != bool(expected) or (expected and
                    selected[0] != {"source_id": expected[0]["source_id"],
                                    "line": expected[0]["line"]}):
                raise RuntimeError(f"frozen 300 source priority drift: {entry['normalized_word']}")
            if selected:
                priority_300["selected_zh" if zh else "selected_wikdict"] += 1
    plan_by_word = {entry["normalized_word"]: entry for entry in plan["entries"]}
    for old in frozen_plan["entries"]:
        fresh = plan_by_word[old["normalized_word"]]
        for field in ("meaning", "phonetic"):
            old_positions = [(item["source_id"], item["line"], item["raw_value"])
                             for item in old["evidence"][field]
                             if not full_wikdict or item["source_id"] != WIKDICT]
            fresh_positions = [(item["source_id"], item["line"], item["raw_value"])
                               for item in fresh["evidence"][field]
                               if not full_wikdict or item["source_id"] != WIKDICT]
            if old_positions != fresh_positions:
                raise RuntimeError(f"frozen evidence drift: {old['normalized_word']} {field}")
    plan_path = package / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    env = dict(os.environ, VOCAB_DATA_DIR=str(db.parent),
               VOCAB_REAL_DATA_DIR=str(db.parent), PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, "-m", "alembic", "-c", str(ROOT / "backend/alembic.ini"),
                    "-x", f"db_url=sqlite:///{db.as_posix()}", "upgrade", "head"],
                   cwd=ROOT / "backend", env=env, check=True)
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    with Session(engine) as session:
        admin = User(username="phase29-full-isolated-reviewer", role="admin", is_active=True)
        lexicon = Lexicon(name=LEXICON_NAME, visibility="public", source_type="phase29-full")
        session.add_all((admin, lexicon))
        session.commit()
        imported = confirm_plan(session, plan=plan, administrator=admin, source_root=package)
        actual = session.scalars(select(LexiconEntry).where(
            LexiconEntry.lexicon_id == lexicon.id).order_by(LexiconEntry.sequence)).all()
        actual_words = [entry.normalized_word for entry in actual]
        actual_sequence = [entry.sequence for entry in actual]
        if (actual_words != expected_words
                or actual_sequence != list(range(1, len(expected_words) + 1))):
            raise RuntimeError("database order differs from NETEM first-occurrence order")
        if full_wikdict:
            for entry in actual:
                planned = plan_by_word[entry.normalized_word]
                chosen = planned["default_evidence"]["meaning"]
                expected_raw = [item["raw_value"] for item in planned["evidence"]["meaning"]
                                if any(item["source_id"] == ref["source_id"] and
                                       item["line"] == ref["line"] for ref in chosen)]
                if entry.source_meanings != expected_raw:
                    raise RuntimeError(f"stored source default drift: {entry.normalized_word}")
        if not full_wikdict and any(entry.source_meanings for entry in actual
                                    if entry.normalized_word not in frozen_words):
            raise RuntimeError("outside-300 word received an unpreserved meaning")
        artifact_names = {artifact.id: artifact.name for artifact in session.scalars(
            select(SourceArtifact))}
        preserved_meaning_rows = {}
        for row in session.scalars(select(EntrySourceEvidence).where(
            EntrySourceEvidence.field_kind == "meaning")):
            preserved_meaning_rows.setdefault(row.normalized_word, []).append((
                artifact_names[row.source_artifact_id], row.row_locator, row.raw_text,
                row.source_revision, bool(row.selected_for_default),
            ))
        source_names = {source["source_id"]: source["file"]["name"]
                        for source in plan["sources"]}
        for entry in planned_300:
            selected = {(item["source_id"], item["line"])
                        for item in entry["default_evidence"]["meaning"]}
            expected_rows = sorted((
                source_names[item["source_id"]], item["line"], item["raw_value"],
                item.get("source_revision", ""),
                (item["source_id"], item["line"]) in selected,
            ) for item in entry["evidence"]["meaning"])
            actual_rows = sorted(preserved_meaning_rows.get(entry["normalized_word"], []))
            if actual_rows != expected_rows:
                raise RuntimeError(f"source evidence not fully retained: {entry['normalized_word']}")
        evidence_count = session.scalar(select(func.count()).select_from(EntrySourceEvidence))
        selected_counts = {}
        for name in ("zhwiktionary-v4en.csv", "wikdict.csv"):
            artifact_id = session.scalar(select(SourceArtifact.id).where(SourceArtifact.name == name))
            selected_counts[name] = session.scalar(select(func.count()).select_from(
                EntrySourceEvidence).where(
                    EntrySourceEvidence.field_kind == "meaning",
                    EntrySourceEvidence.selected_for_default.is_(True),
                    EntrySourceEvidence.source_artifact_id == artifact_id,
                ))
    representative = ({"skipped": True} if skip_representative_meanings
                      else _check_representative_api(engine, db))
    with sqlite3.connect(db) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != "ok" or foreign_key_violations:
        raise RuntimeError(f"database integrity failed: {integrity}, {foreign_key_violations[:3]}")
    result = {
        "source_rows": len(original_rows), "unique_membership": len(membership),
        "duplicates": duplicates,
        "frozen_300_coverage": priority_300 if full_wikdict else coverage,
        "unresolved": unresolved, "import": imported,
        "actual_entries": len(actual_words), "order_matches": actual_words == expected_words,
        "sequence_contiguous": actual_sequence == list(range(1, len(expected_words) + 1)),
        "first_five": actual_words[:5], "last_five": actual_words[-5:],
        "csv_evidence": evidence_count, "selected_meaning_counts": selected_counts,
        "frozen_300_evidence_exact": True,
        "representative": representative,
        "integrity_check": integrity, "foreign_key_violations": len(foreign_key_violations),
    }
    if full_wikdict:
        selected_by_word = {entry.normalized_word: entry.source_meanings for entry in actual}
        uncovered_normalized = {word.strip().casefold() for word in uncovered}
        without_source = [word for word in expected_words if not selected_by_word[word]]
        if any(word not in uncovered_normalized and word not in frozen_words
               for word in without_source):
            raise RuntimeError("word with WikDict original lost its source meaning")
        key_rows = [row for row in wikdict_rows if row["word"].strip().casefold() == "key"]
        if len(key_rows) != 1 or key_rows[0]["wikdict_entry_index"] != "13332|13333":
            raise RuntimeError("key original StarDict positions changed")
        result["full_wikdict"] = {
            "zip_sha256": WIKDICT_ZIP_SHA256,
            "matched": len(wikdict_rows), "uncovered_count": len(uncovered),
            "uncovered_file": str(package / "wikdict-uncovered.json"),
            "without_source_meaning": len(without_source),
            "frozen_300_priority": priority_300,
            "key_original": key_rows[0],
        }
        original = wikdict_original_entries(WIKDICT_ZIP, {13332, 13333})
        if [item["headword"] for item in original] != ["key", "key"]:
            raise RuntimeError("key original headword mismatch")
        (package / "key-original.json").write_text(
            json.dumps({"zip_sha256": WIKDICT_ZIP_SHA256, "entries": original},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        result["full_wikdict"]["key_original_file"] = str(package / "key-original.json")
    (db.parent / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    flags = set(sys.argv[2:])
    if len(sys.argv) < 2 or flags - {"--full-wikdict", "--skip-representative-meanings"}:
        raise SystemExit("Usage: 50_full_lexicon_rehearsal.py NEW_DB_PATH "
                         "[--full-wikdict] [--skip-representative-meanings]")
    main(Path(sys.argv[1]), full_wikdict="--full-wikdict" in flags,
         skip_representative_meanings="--skip-representative-meanings" in flags)
