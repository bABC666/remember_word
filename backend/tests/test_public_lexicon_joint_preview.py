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


# --- the manifest's non-field revision declaration ----------------------------
#
# Exercised through the read-only entry points only: this slice reads the
# declaration, it does not freeze it into a plan or write it anywhere.

REVISION_TEMPLATE = "https://zh.wiktionary.org/w/index.php?oldid={revision}"


def _manifest_with(root: Path, source: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "manifest.json"
    path.write_text(
        json.dumps({"sources": [{"id": "pinned", "file": "words.csv", **source}]}),
        encoding="utf-8",
    )
    return path


def test_manifest_reads_a_revision_declaration_into_the_frozen_mapping(
    tmp_path: Path,
) -> None:
    from app.services.public_lexicon_joint_preview import preview_manifest

    root = tmp_path / "sources"
    root.mkdir(parents=True, exist_ok=True)
    (root / "words.csv").write_text(
        "head,cn,oldid\nadmit,承认,6588944\n", encoding="utf-8"
    )
    manifest = _manifest_with(root, {
        "columns": {"word": "head", "meaning": "cn"},
        "encoding": "utf-8",
        "revision": {"column": "oldid", "url_template": REVISION_TEMPLATE},
    })

    report = preview_manifest(manifest, source_root=root)
    preview = report["sources"][0]["preview"]

    assert preview["mapping"]["revision"] == {
        "column": "oldid", "value": "", "url_template": REVISION_TEMPLATE,
    }
    assert preview["rows"][0]["source_revision"] == "6588944", (
        "the declaration reaches the row it describes"
    )


def test_manifest_reads_the_whole_file_revision_form(tmp_path: Path) -> None:
    from app.services.public_lexicon_joint_preview import preview_manifest

    commit = "70dc6b68c855f21e666a7a291ff8ead5ca1f7b44"
    root = tmp_path / "sources"
    root.mkdir(parents=True)
    (root / "words.csv").write_text("head\nabnormal\n", encoding="utf-8")
    manifest = _manifest_with(root, {
        "columns": {"word": "head"},
        "encoding": "utf-8",
        "revision": {
            "value": commit,
            "url_template": "https://github.com/exam-data/NETEMVocabulary/tree/{revision}",
        },
    })

    report = preview_manifest(manifest, source_root=root)
    preview = report["sources"][0]["preview"]

    assert preview["mapping"]["revision"]["value"] == commit
    assert preview["rows"][0]["source_revision"] == commit


def test_a_manifest_without_a_revision_block_is_unchanged(tmp_path: Path) -> None:
    """Old manifests keep working, and keep producing the mapping they always did."""
    from app.services.public_lexicon_joint_preview import load_manifest, preview_manifest

    root = tmp_path / "sources"
    root.mkdir(parents=True)
    (root / "words.csv").write_text("head,cn\nadmit,承认\n", encoding="utf-8")
    manifest = _manifest_with(root, {
        "columns": {"word": "head", "meaning": "cn"}, "encoding": "utf-8",
    })

    specs = load_manifest(manifest, source_root=root)
    assert specs.sources[0].mapping.revision is None

    preview = preview_manifest(manifest, source_root=root)["sources"][0]["preview"]
    assert set(preview["mapping"]) == {
        "columns", "required_fields", "encoding", "delimiter"
    }
    assert "source_revision" not in preview["rows"][0], (
        "a source that declares no revision must produce the byte-identical report "
        "it produced before the declaration existed"
    )


@pytest.mark.parametrize(("revision", "message"), [
    ({"column": "oldid", "value": "1", "url_template": REVISION_TEMPLATE},
     r"exactly one of column or value"),
    ({"url_template": REVISION_TEMPLATE}, r"exactly one of column or value"),
    ({"column": "oldid"}, r"needs a url_template"),
    ({"column": "oldid", "url_template": "https://example.org/page"},
     r"exactly one \{revision\} placeholder, found 0"),
    ({"column": "oldid", "url_template": "http://example.org/{revision}"},
     r"must start with https://"),
    ({"column": "oldid", "url_template": "https://example.org/a b/{revision}"},
     r"whitespace or a control character"),
    ({"column": " oldid", "url_template": REVISION_TEMPLATE},
     r"leading or trailing whitespace"),
    ({"value": "70dc6b68\u0000", "url_template": REVISION_TEMPLATE},
     r"control character"),
    ({"column": "c" * 65, "url_template": REVISION_TEMPLATE},
     r"longer than 64 characters"),
    ({"column": "oldid", "url_template": "https://e.org/" + "a" * 400 + "{revision}"},
     r"longer than 400 characters"),
])
def test_manifest_refuses_a_revision_that_could_not_produce_a_link(
    tmp_path: Path, revision: dict, message: str
) -> None:
    """Every refusal names the source it came from."""
    from app.services.public_lexicon_joint_preview import preview_manifest

    root = tmp_path / "sources"
    root.mkdir(parents=True)
    (root / "words.csv").write_text("head,oldid\nadmit,6588944\n", encoding="utf-8")
    manifest = _manifest_with(root, {
        "columns": {"word": "head"}, "encoding": "utf-8", "revision": revision,
    })

    with pytest.raises(ValueError, match=f"source 'pinned' revision.*{message}"):
        preview_manifest(manifest, source_root=root)


def test_manifest_refuses_an_unknown_revision_key_rather_than_ignoring_it(
    tmp_path: Path,
) -> None:
    """A typo must be named, not silently recorded as "no template"."""
    from app.services.public_lexicon_joint_preview import preview_manifest

    root = tmp_path / "sources"
    root.mkdir(parents=True)
    (root / "words.csv").write_text("head,oldid\nadmit,6588944\n", encoding="utf-8")
    manifest = _manifest_with(root, {
        "columns": {"word": "head"},
        "encoding": "utf-8",
        "revision": {
            "column": "oldid", "url": "https://example.org/{revision}",
            "url_template": REVISION_TEMPLATE,
        },
    })

    with pytest.raises(ValueError) as error:
        preview_manifest(manifest, source_root=root)
    assert "unknown field(s) ['url']" in str(error.value)
    assert "known fields are ['column', 'value', 'url_template']" in str(error.value)


@pytest.mark.parametrize(("revision", "message"), [
    ("CC BY-SA", r"revision must be an object"),
    (["oldid"], r"revision must be an object"),
    ({"column": 42}, r"revision\.column must be a string"),
    ({"column": "oldid", "url_template": None},
     r"revision\.url_template must be a string"),
])
def test_manifest_refuses_a_revision_block_of_the_wrong_shape(
    tmp_path: Path, revision, message: str
) -> None:
    from app.services.public_lexicon_joint_preview import preview_manifest

    root = tmp_path / "sources"
    root.mkdir(parents=True)
    (root / "words.csv").write_text("head,oldid\nadmit,6588944\n", encoding="utf-8")
    manifest = _manifest_with(root, {
        "columns": {"word": "head"}, "encoding": "utf-8", "revision": revision,
    })

    with pytest.raises(TypeError, match=f"source 'pinned' {message}"):
        preview_manifest(manifest, source_root=root)
