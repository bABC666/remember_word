"""Targeted checks for the frozen 300 word adjudication and its citations."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/phase29/frozen300.py"


def _module():
    spec = importlib.util.spec_from_file_location("frozen300", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_list_has_one_review_record_per_word_and_bounded_groups():
    result = _module().resolve(ROOT)
    assert len(result) == 300
    assert len({row["word"] for row in result}) == 300
    for row in result:
        for group in row["groups"]:
            assert 1 <= len(group["meanings"]) <= 3
            assert group["pos_key"]
            for sense in group["meanings"]:
                if sense["basis"] == "Z":
                    assert sense["language"] == "en"
                    assert sense["source_locator"].startswith("zhwiktionary:")
                    assert sense["raw_line"]
                elif sense["basis"] == "W":
                    assert sense["source_locator"].startswith("wikdict:")
                    assert sense["raw_line"]
                else:
                    assert sense["basis"] == "AI"
                    assert not sense["source_locator"]
                    assert sense["decision"] == "pending"


def test_trial_is_reused_and_cross_language_and_key_are_withheld():
    rows = {row["word"]: row for row in _module().resolve(ROOT)}
    assert [m["text"] for g in rows["play"]["groups"] for m in g["meanings"]] == [
        "玩", "演奏", "播放", "剧"
    ]
    assert rows["fertiliser"]["groups"][0]["meanings"][0]["decision"] == "pending"
    assert any("跨语言" in item["reason"] and ":20" in item["locator"]
               for item in rows["fertiliser"]["omitted"])
    assert all(m["language"] == "en" for row in rows.values()
               for group in row["groups"] for m in group["meanings"] if m["basis"] == "Z")
    assert "key" not in rows


def test_source_needle_resolver_does_not_cross_language_sections():
    module = _module()
    candidates = [
        {"language": "fr", "text": "肥料", "raw_line": "#肥料", "line_no": 20},
        {"language": "en", "text": "施肥", "raw_line": "#施肥", "line_no": 5},
    ]
    assert module.find_english_candidate(candidates, "肥料") is None
    assert module.find_english_candidate(candidates, "施肥")["line_no"] == 5


def test_isolated_proposal_and_confirmation_keeps_unsupported_candidate_hidden(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "run_frozen300", ROOT / "tools/phase29/run_frozen300.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run(ROOT, tmp_path / "isolated", words=[
        "prior", "play", "performance", "refrain", "digest", "mutter", "fertiliser"
    ])
    assert report["attempted_words"] == 7
    assert report["confirmed_words"] == 6
    assert report["pending_words"] >= 1
    assert report["by_word"]["fertiliser"]["confirmed"] == 0
    assert report["by_word"]["play"]["confirmed"] == 4
    assert report["by_word"]["prior"]["confirmed"] == 3
    assert report["by_word"]["performance"]["confirmed"] == 3
    assert report["by_word"]["refrain"]["confirmed"] == 2
    assert report["by_word"]["digest"]["confirmed"] == 2
    database = tmp_path / "isolated" / "vocab.db"
    assert database.is_file()
    with sqlite3.connect(database) as connection:
        assert connection.execute("select count(*) from entry_concise_meaning "
                                  "where status='confirmed'").fetchone()[0] == report["confirmed_senses"]
        assert connection.execute("select count(*) from entry_concise_meaning "
                                  "where provenance_kind='ai_supplement' and status='confirmed'")\
            .fetchone()[0] == 0


def test_key_evidence_separates_supported_key_from_unproved_crucial_sense():
    evidence = _module().key_evidence(ROOT)
    assert evidence["word"] == "key"
    assert evidence["inside_frozen_300"] is False
    assert evidence["senses"]["钥匙"]["supported"] is True
    assert evidence["senses"]["关键"]["supported"] is False
    assert evidence["senses"]["钥匙"]["stardict_entries"]


def test_isolated_runner_refuses_application_data_directory():
    spec = importlib.util.spec_from_file_location(
        "run_frozen300", ROOT / "tools/phase29/run_frozen300.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="isolated"):
        module.run(ROOT, ROOT / "data/phase29-frozen300-oops", words=["prior"])
