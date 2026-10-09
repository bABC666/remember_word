"""Keep gap parsing tied to exact English source lines and the exclusion rule."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools/phase29/resolve_default_gaps.py"
SPEC = importlib.util.spec_from_file_location("default_gap_resolution", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_bare_english_line_and_numbered_line_keep_wikitext_positions():
    text = "==英语==\n===發音===\n* {{IPA|en|/a/}}\n包括\n# [[包含]]\n==法語==\n# [[法语释义]]"
    assert [(row["meaning"], row["wikitext_line"]) for row in MODULE.extract(text)] == [
        ("包括", 4), ("包含", 5)]


def test_template_parameters_and_examples_are_not_definitions():
    text = "==英語==\n{{en-1名|\n一格单数='''X-ray'''\n}}\n# [[X射线]]\n人离岸时, 山峰向后退去。"
    assert [row["meaning"] for row in MODULE.extract(text)] == ["X射线"]


def test_exact_page_spelling_template_translation_is_read_without_looking_up_other_word():
    text = "==英語==\n# {{standard spelling of|en|aluminium|from=American form|t=鋁}}"
    assert [(row["meaning"], row["parse_method"]) for row in MODULE.extract(text)] == [
        ("鋁", "exact_page_template_translation")]


def test_cedict_marked_page_is_excluded_wholesale():
    assert MODULE.extract("==英語==\n似乎\n{{CC-CEDICT}}") == []
