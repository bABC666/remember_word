"""The full-list rehearsal keeps membership separate from source wording."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/phase29/rehearsal/50_full_lexicon_rehearsal.py"


def _script():
    spec = importlib.util.spec_from_file_location("phase29_full_rehearsal", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_membership_uses_first_normalized_occurrence_and_never_exports_gloss() -> None:
    module = _script()
    rows = [
        {"序号": 1, "单词": "may", "词频": 10, "释义": "must not export"},
        {"序号": 2, "单词": "prior", "词频": 9, "释义": "also not"},
        {"序号": 3, "单词": "May", "词频": 0, "释义": "month"},
    ]
    membership, duplicates = module.membership_rows(rows)
    assert membership == [
        {"word": "may", "netem_rank": 1, "freq_claimed_mixed_exam_corpus": 10},
        {"word": "prior", "netem_rank": 2, "freq_claimed_mixed_exam_corpus": 9},
    ]
    assert duplicates == [{"word": "May", "netem_rank": 3,
                           "kept_word": "may", "kept_rank": 1}]
    assert "释义" not in json.dumps(membership, ensure_ascii=False)


def test_frozen_300_selection_prefers_zh_and_lists_missing() -> None:
    module = _script()
    def item(word, sources):
        return {"normalized_word": word, "evidence": {"meaning": [
            {"source_id": source, "line": line, "raw_value": value}
            for source, line, value in sources
        ]}}
    entries = [
        item("prior", [("wikdict-en-zh-2026-06-23", 37, "W"),
                       ("zhwiktionary-pinned-oldid", 139, "Z")]),
        item("only-w", [("wikdict-en-zh-2026-06-23", 40, "W")]),
        item("fertiliser", []),
    ]
    decisions, coverage, unresolved = module.select_frozen_meaning_fields(entries)
    assert [(d["normalized_word"], d["evidence"][0]) for d in decisions] == [
        ("prior", {"source_id": "zhwiktionary-pinned-oldid", "line": 139}),
        ("only-w", {"source_id": "wikdict-en-zh-2026-06-23", "line": 40}),
    ]
    assert coverage == {"both": 1, "zh_only": 0, "wikdict_only": 1, "neither": 1}
    assert unresolved == [{"word": "fertiliser", "reason": "no_preserved_meaning"}]


def test_wikdict_zip_rows_keep_original_positions_and_flatten_lines(tmp_path) -> None:
    module = _script()
    archive = tmp_path / "wikdict.zip"
    bodies = ["<div><div>钥匙</div></div>", "<div><div>鑰匙</div>\n<div>钥匙</div></div>"]
    data = "".join(bodies).encode()
    index = b""
    offset = 0
    for body in bodies:
        encoded = body.encode()
        index += b"key\0" + offset.to_bytes(4, "big") + len(encoded).to_bytes(4, "big")
        offset += len(encoded)
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("wikdict-en-zh/stardict.idx", index)
        z.writestr("wikdict-en-zh/stardict.dict", data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    rows, uncovered = module.wikdict_backfill_rows(
        archive, [{"word": "key"}, {"word": "absent"}], digest)
    assert uncovered == ["absent"]
    assert rows == [{"word": "key", "wikdict_meaning": "钥匙；鑰匙",
                     "wikdict_headword": "key|key", "wikdict_entry_index": "0|1",
                     "wikdict_entry_offset": f"0|{len(bodies[0].encode())}",
                     "match_rule": "casefold_exact",
                     "wikdict_package_sha256": digest}]


def test_wikdict_backfill_rejects_punctuation_collision(tmp_path) -> None:
    module = _script()
    archive = tmp_path / "wikdict.zip"
    body = "<div>再次</div>".encode()
    index = b"re-solve\0" + (0).to_bytes(4, "big") + len(body).to_bytes(4, "big")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("wikdict-en-zh/stardict.idx", index)
        z.writestr("wikdict-en-zh/stardict.dict", body)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    rows, uncovered = module.wikdict_backfill_rows(archive, [{"word": "resolve"}], digest)
    assert rows == []
    assert uncovered == ["resolve"]


def test_backfill_selection_keeps_frozen_zh_and_uses_wikdict_elsewhere() -> None:
    module = _script()
    frozen = [{"normalized_word": "prior", "evidence": {"meaning": [
        {"source_id": module.ZH, "line": 7, "raw_value": "先前的"}]}}]
    decisions = module.select_backfill_meaning_fields(
        frozen, {"prior": 2, "key": 3}, ["prior", "key", "absent"])
    assert [(row["normalized_word"], row["evidence"][0]) for row in decisions] == [
        ("prior", {"source_id": module.ZH, "line": 7}),
        ("key", {"source_id": module.WIKDICT, "line": 3}),
    ]
