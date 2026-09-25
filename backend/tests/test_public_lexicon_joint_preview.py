"""Synthetic multi-source preview contracts; no import confirmation or database writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.public_lexicon_preview import PreviewMapping


def test_joint_preview_preserves_conflicting_meanings_and_ignores_input_order(
    tmp_path: Path,
) -> None:
    from app.services.public_lexicon_joint_preview import SourceSpec, preview_sources

    (tmp_path / "main.csv").write_text(
        "head,cn\nApple,苹果；果实\n", encoding="utf-8"
    )
    (tmp_path / "supplement.csv").write_text(
        "term,translation,ipa,pos\n apple ,苹果公司,/ˈæpəl/,noun\n", encoding="utf-8"
    )
    sources = [
        SourceSpec(
            "primary", Path("main.csv"),
            PreviewMapping(columns={"word": "head", "meaning": "cn"}),
        ),
        SourceSpec(
            "supplement", Path("supplement.csv"),
            PreviewMapping(columns={"word": "term", "meaning": "translation",
                                    "phonetic": "ipa", "part_of_speech": "pos"}),
        ),
    ]

    report = preview_sources(sources, source_root=tmp_path)
    reversed_report = preview_sources(list(reversed(sources)), source_root=tmp_path)

    assert report == reversed_report
    assert report["summary"] == {
        "source_files": 2, "candidate_words": 1,
        "meaning_conflicts": 1, "missing_field_words": 0,
    }
    entry = report["entries"][0]
    assert entry["normalized_word"] == "apple"
    assert entry["conflicts"] == [{
        "field": "meaning", "raw_values": ["苹果；果实", "苹果公司"]
    }]
    assert entry["missing_fields"] == []
    assert [(e["source_id"], e["raw_word"], e["raw_value"], e["line"])
            for e in entry["fields"]["meaning"]] == [
        ("primary", "Apple", "苹果；果实", 2),
        ("supplement", " apple ", "苹果公司", 2),
    ]
    assert entry["fields"]["meaning"][0]["file_sha256"] == report["sources"][0]["preview"]["file"]["sha256"]
    assert entry["fields"]["phonetic"][0]["raw_value"] == "/ˈæpəl/"
    assert entry["fields"]["part_of_speech"][0]["raw_value"] == "noun"
    assert all(e["raw_value"] != "苹果；果实；苹果公司" for e in entry["fields"]["meaning"])
    assert len(report["report_sha256"]) == 64


def test_joint_preview_reports_missing_meaning_and_keeps_source_errors(tmp_path: Path) -> None:
    from app.services.public_lexicon_joint_preview import SourceSpec, preview_sources

    (tmp_path / "words.csv").write_text("head\nBare\n", encoding="utf-8")
    (tmp_path / "broken.csv").write_text("head,cn\nBare,\nOther,valid\n", encoding="utf-8")
    sources = [
        SourceSpec("words", Path("words.csv"), PreviewMapping(columns={"word": "head"})),
        SourceSpec(
            "broken", Path("broken.csv"),
            PreviewMapping(columns={"word": "head", "meaning": "cn"},
                           required_fields=("word", "meaning")),
        ),
    ]

    report = preview_sources(sources, source_root=tmp_path)

    assert report["entries"][0]["normalized_word"] == "bare"
    assert report["entries"][0]["missing_fields"] == ["meaning"]
    assert [(e["source_id"], e["raw_value"]) for e in
            report["entries"][0]["fields"]["meaning"]] == [("broken", "")]
    assert report["entries"][1]["normalized_word"] == "other"
    assert report["sources"][0]["preview"]["rows"][0]["issues"] == [
        {"code": "missing_value", "field": "meaning"}
    ]


def test_joint_preview_reports_missing_meaning_without_another_source(tmp_path: Path) -> None:
    from app.services.public_lexicon_joint_preview import SourceSpec, preview_sources

    (tmp_path / "only.csv").write_text("head,cn\nSolo,\n", encoding="utf-8")
    source = SourceSpec(
        "only", Path("only.csv"),
        PreviewMapping(columns={"word": "head", "meaning": "cn"},
                       required_fields=("word", "meaning")),
    )

    report = preview_sources([source], source_root=tmp_path)

    assert report["summary"]["candidate_words"] == 1
    assert report["summary"]["missing_field_words"] == 1
    assert report["entries"][0]["normalized_word"] == "solo"
    assert report["entries"][0]["missing_fields"] == ["meaning"]
    assert report["entries"][0]["fields"]["meaning"][0]["raw_value"] == ""
    assert report["sources"][0]["preview"]["rows"][0]["issues"] == [
        {"code": "missing_value", "field": "meaning"}
    ]


def test_joint_preview_keeps_equal_meanings_from_both_sources_without_conflict(
    tmp_path: Path,
) -> None:
    from app.services.public_lexicon_joint_preview import SourceSpec, preview_sources

    (tmp_path / "a.csv").write_text("head,cn\nWord,词\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("head,cn\nword,词\n", encoding="utf-8")
    sources = [
        SourceSpec(source_id, Path(f"{source_id}.csv"),
                   PreviewMapping(columns={"word": "head", "meaning": "cn"}))
        for source_id in ("a", "b")
    ]

    entry = preview_sources(sources, source_root=tmp_path)["entries"][0]

    assert entry["conflicts"] == []
    assert [e["source_id"] for e in entry["fields"]["meaning"]] == ["a", "b"]
    assert [e["raw_word"] for e in entry["fields"]["word"]] == ["Word", "word"]


def test_joint_preview_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    from app.services.public_lexicon_joint_preview import SourceSpec, preview_sources

    (tmp_path / "a.csv").write_text("head\nWord\n", encoding="utf-8")
    source = SourceSpec("same", Path("a.csv"), PreviewMapping(columns={"word": "head"}))

    with pytest.raises(ValueError, match="duplicate_source_id"):
        preview_sources([source, source], source_root=tmp_path)


def test_joint_preview_keeps_missing_mapped_header_in_source_report(tmp_path: Path) -> None:
    from app.services.public_lexicon_joint_preview import SourceSpec, preview_sources

    (tmp_path / "primary.csv").write_text("head,cn\nword,词\n", encoding="utf-8")
    (tmp_path / "bad.csv").write_text("head,ipa\nword,/wɜːd/\n", encoding="utf-8")
    sources = [
        SourceSpec("primary", Path("primary.csv"),
                   PreviewMapping(columns={"word": "head", "meaning": "cn"})),
        SourceSpec("bad", Path("bad.csv"),
                   PreviewMapping(columns={"word": "head", "meaning": "cn"})),
    ]

    report = preview_sources(sources, source_root=tmp_path)

    assert report["sources"][0]["source_id"] == "bad"
    assert report["sources"][0]["preview"]["issues"] == [
        {"line": 1, "code": "missing_column", "field": "meaning"}
    ]
    assert report["entries"][0]["fields"]["meaning"][0]["source_id"] == "primary"


def test_joint_cli_manifest_is_read_only_and_confined(world, tmp_path: Path, capsys, monkeypatch) -> None:
    from app import cli
    from app.models import LexiconEntry, ReviewEvent, UserWordState

    root = tmp_path / "sources"
    root.mkdir()
    source = root / "words.csv"
    source.write_text("head,cn\nExample,完整释义\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "required_fields": ["meaning"],
        "sources": [{"id": "test", "file": "words.csv", "columns": {
            "word": "head", "meaning": "cn",
        }}],
    }), encoding="utf-8")
    before = (source.read_bytes(), manifest.read_bytes())
    with world.session() as session:
        before_rows = tuple(
            session.query(model).count() for model in (LexiconEntry, UserWordState, ReviewEvent)
        )

    def unexpected_database_call(*_args, **_kwargs):
        raise AssertionError("joint preview must not initialize a database")

    monkeypatch.setattr(cli, "get_settings", unexpected_database_call)
    monkeypatch.setattr(cli, "verify_schema_revision", unexpected_database_call)

    assert cli.main([
        "public-lexicon", "preview-many", str(manifest), "--source-root", str(root)
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["entries"][0]["fields"]["meaning"][0]["raw_value"] == "完整释义"
    assert (source.read_bytes(), manifest.read_bytes()) == before
    with world.session() as session:
        after_rows = tuple(
            session.query(model).count() for model in (LexiconEntry, UserWordState, ReviewEvent)
        )
    assert after_rows == before_rows

    escaped_manifest = tmp_path / "outside.json"
    escaped_manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    assert cli.main([
        "public-lexicon", "preview-many", str(escaped_manifest), "--source-root", str(root)
    ]) == 2
    assert "outside_source_root" in capsys.readouterr().err
