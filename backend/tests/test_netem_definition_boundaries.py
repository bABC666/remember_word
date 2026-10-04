"""Regression cases for adopted NETEM source section leakage (no database)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/phase29"))
from resolve_default_gaps import extract
from zhwiktionary_clean_measure import clean


def test_usage_references_and_nested_synonyms_are_not_definitions():
    text = """==英语==
===形容词===
# 生病的
====近义词====
* 亦参见[[Thesaurus:diseased]]
=====补充=====
# 这里仍是近义词说明
===名词===
# 呕吐物
===用法===
# prefer A to B 喜欢A胜过B
===参考资料===
* [https://example.org 中文资料]
==法语==
# 法语释义
"""
    assert clean(text)[0] == ["生病的", "呕吐物"]
    assert [r["meaning"] for r in extract(text)] == ["生病的", "呕吐物"]
    assert [r["wikitext_line"] for r in extract(text)] == [3, 9]


def test_legacy_bare_lines_under_pronunciation_remain_available():
    text = "==英语==\n===發音===\n* {{IPA|en|/x/}}\n现代\n==德语==\n# 错误语言\n"
    assert [r["meaning"] for r in extract(text)] == ["现代"]
    assert extract(text)[0]["wikitext_line"] == 4


def test_numbered_etymology_can_contain_parts_of_speech():
    text = "==英语==\n===词源 1===\n* 来自拉丁语\n====名词====\n# 合法释义\n=====用法说明=====\n* 排除说明\n====动词====\n# 另一释义\n"
    assert clean(text)[0] == ["合法释义", "另一释义"]


def test_fixed_adopted_usage_and_alternative_form_heading_variants():
    text = "==英语==\n===动词===\n# 遵守\n===使用說明===\n* 動詞是不及物動詞，但經常帶介詞\n===其他词形===\n* whiskey：不同拼法亦用于区分产地\n"
    assert clean(text)[0] == ["遵守"]
    assert [row["meaning"] for row in extract(text)] == ["遵守"]
