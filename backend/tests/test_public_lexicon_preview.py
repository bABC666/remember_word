"""Synthetic-file contracts for the read-only public lexicon preview slice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_preview_maps_fields_preserves_raw_values_and_flags_duplicate(tmp_path: Path) -> None:
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    raw = (
        'head,cn,ipa\nApple,"苹果；果实",/ˈæpəl/\n'
        ' apple ,苹果,/ˈæpəl/\na-pple,另一词形,\n'
    ).encode()
    source = tmp_path / "synthetic.csv"
    source.write_bytes(raw)

    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "head", "meaning": "cn", "phonetic": "ipa"},
            required_fields=("word", "meaning"),
        ),
    )

    assert report["file"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["file"]["byte_size"] == len(raw)
    assert report["summary"] == {
        "total_rows": 3, "valid_rows": 2, "duplicate_rows": 1, "error_rows": 1
    }
    assert report["rows"][0]["line"] == 2
    assert report["rows"][0]["raw"] == 'Apple,"苹果；果实",/ˈæpəl/'
    assert report["rows"][0]["values"] == {
        "word": "Apple", "meaning": "苹果；果实", "phonetic": "/ˈæpəl/"
    }
    assert report["rows"][0]["normalized_word"] == "apple"
    assert report["rows"][1]["normalized_word"] == "apple"
    assert report["rows"][1]["issues"] == [
        {"code": "duplicate_word", "field": "word", "first_line": 2}
    ]
    assert report["rows"][2]["normalized_word"] == "a-pple"
    assert report["rows"][2]["issues"] == []


def test_preview_reports_missing_mapped_header_without_candidates(tmp_path: Path) -> None:
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    source = tmp_path / "no-meaning.csv"
    source.write_text("head,ipa\nword,/wɜːd/\n", encoding="utf-8")

    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "head", "meaning": "cn"},
            required_fields=("word", "meaning"),
        ),
    )

    assert report["rows"] == []
    assert report["summary"]["valid_rows"] == 0
    assert report["issues"] == [{"line": 1, "code": "missing_column", "field": "meaning"}]


def test_preview_reports_bad_encoding_with_original_byte_fingerprint(tmp_path: Path) -> None:
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    raw = b"head,cn\nword,\xff\n"
    source = tmp_path / "bad-encoding.csv"
    source.write_bytes(raw)

    report = preview_file(
        source,
        PreviewMapping(columns={"word": "head", "meaning": "cn"}, encoding="utf-8"),
    )

    assert report["file"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["rows"] == []
    assert report["issues"][0]["code"] == "decode_error"
    assert report["issues"][0]["line"] == 2


def test_preview_reports_each_bad_row_and_keeps_later_rows(tmp_path: Path) -> None:
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    source = tmp_path / "bad-rows.tsv"
    source.write_text(
        'head\tcn\nfirst\t释义\nsecond\t\nthird\t释义\textra\n"broken\t释义\nlast\t保留\n',
        encoding="utf-8",
    )

    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "head", "meaning": "cn"},
            required_fields=("word", "meaning"),
            delimiter="\t",
        ),
    )

    assert report["summary"] == {
        "total_rows": 5, "valid_rows": 2, "duplicate_rows": 0, "error_rows": 3
    }
    assert [(row["line"], [issue["code"] for issue in row["issues"]]) for row in report["rows"]] == [
        (2, []),
        (3, ["missing_value"]),
        (4, ["column_count"]),
        (5, ["csv_error"]),
        (6, []),
    ]
    assert report["rows"][-1]["values"]["meaning"] == "保留"


def test_preview_reports_blank_line_and_continues(tmp_path: Path) -> None:
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    source = tmp_path / "blank.csv"
    source.write_text("head,cn\nfirst,首词\n\nlast,末词\n", encoding="utf-8")

    report = preview_file(
        source, PreviewMapping(columns={"word": "head", "meaning": "cn"})
    )

    assert report["summary"] == {
        "total_rows": 3, "valid_rows": 2, "duplicate_rows": 0, "error_rows": 1
    }
    assert report["rows"][1]["line"] == 3
    assert report["rows"][1]["issues"][0]["code"] == "column_count"
    assert report["rows"][2]["normalized_word"] == "last"


def test_preview_does_not_change_business_rows_or_source_file(world, tmp_path: Path) -> None:
    from app.models import LexiconEntry, ReviewEvent, UserWordState
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    source = tmp_path / "only-preview.csv"
    source.write_text("head,cn\nnewword,完整释义\n", encoding="utf-8")
    before_bytes = source.read_bytes()
    with world.session() as session:
        before = tuple(
            session.query(model).count() for model in (LexiconEntry, UserWordState, ReviewEvent)
        )

    report = preview_file(
        source, PreviewMapping(columns={"word": "head", "meaning": "cn"})
    )

    with world.session() as session:
        after = tuple(
            session.query(model).count() for model in (LexiconEntry, UserWordState, ReviewEvent)
        )
    assert report["summary"]["valid_rows"] == 1
    assert after == before
    assert source.read_bytes() == before_bytes


def test_preview_decodes_explicit_gb18030_without_rewriting_source(tmp_path: Path) -> None:
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    source = tmp_path / "legacy.csv"
    raw = "head,cn\nterm,完整中文释义\n".encode("gb18030")
    source.write_bytes(raw)

    report = preview_file(
        source,
        PreviewMapping(columns={"word": "head", "meaning": "cn"}, encoding="gb18030"),
    )

    assert report["file"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["rows"][0]["values"]["meaning"] == "完整中文释义"
    assert source.read_bytes() == raw


def test_cli_preview_does_not_initialize_or_verify_database(tmp_path: Path, capsys, monkeypatch) -> None:
    from app import cli

    source = tmp_path / "synthetic.csv"
    source.write_text("head,cn\nExample,完整释义\n", encoding="utf-8")
    source_bytes = source.read_bytes()

    def unexpected_database_call(*_args, **_kwargs):
        raise AssertionError("preview must not initialize or verify a database")

    monkeypatch.setattr(cli, "get_settings", unexpected_database_call)
    monkeypatch.setattr(cli, "verify_schema_revision", unexpected_database_call)

    result = cli.main([
        "public-lexicon", "preview", str(source),
        "--map", "word=head", "--map", "meaning=cn", "--required", "meaning",
    ])

    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report["summary"]["valid_rows"] == 1
    assert report["rows"][0]["normalized_word"] == "example"
    assert source.read_bytes() == source_bytes
