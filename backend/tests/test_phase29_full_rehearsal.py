"""The full-list rehearsal keeps membership separate from source wording."""

from __future__ import annotations

import importlib.util
import json
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
