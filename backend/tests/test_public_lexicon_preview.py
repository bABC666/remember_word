"""Synthetic-file contracts for the read-only public lexicon preview slice."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


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
        source_root=tmp_path,
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
        source_root=tmp_path,
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
        source_root=tmp_path,
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
        source_root=tmp_path,
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
        source, PreviewMapping(columns={"word": "head", "meaning": "cn"}),
        source_root=tmp_path,
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
        source, PreviewMapping(columns={"word": "head", "meaning": "cn"}),
        source_root=tmp_path,
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
        source_root=tmp_path,
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
        "public-lexicon", "preview", str(source), "--source-root", str(tmp_path),
        "--map", "word=head", "--map", "meaning=cn", "--required", "meaning",
    ])

    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report["summary"]["valid_rows"] == 1
    assert report["rows"][0]["normalized_word"] == "example"
    assert source.read_bytes() == source_bytes


# --- the revision declaration: metadata beside the fields, never a field -------
#
# The mapping digest computed from a mapping that declares nothing. It is what every
# manifest written before the declaration existed produced, so a change to it would
# turn compatible inputs into different source artifacts.
LEGACY_MAPPING = {
    "columns": {"word": "word", "meaning": "zh_meaning", "phonetic": "zh_ipa"},
    "required_fields": ["word"],
    "encoding": "utf-8",
    "delimiter": ",",
}
LEGACY_MAPPING_SHA256 = "199e102233dbbdb9239134a5fe3b1abde686a695aa0e3a5747ab6015b98a70df"

WIKTIONARY_TEMPLATE = "https://zh.wiktionary.org/w/index.php?oldid={revision}"


def test_a_row_carries_its_own_revision_and_the_declaration_is_frozen(
    tmp_path: Path,
) -> None:
    """The column form: one pinned revision per row, taken from the row's own cell."""
    from app.services.public_lexicon_plan import mapping_sha256
    from app.services.public_lexicon_preview import (
        PreviewMapping,
        RevisionDeclaration,
        preview_file,
    )

    source = tmp_path / "pinned.csv"
    source.write_text(
        "head,cn,oldid\n"
        "admit,承认,6588944\n"
        "typist,打字员,\n",
        encoding="utf-8",
    )
    mapping = PreviewMapping(
        columns={"word": "head", "meaning": "cn"},
        encoding="utf-8",
        revision=RevisionDeclaration(
            column="oldid", url_template=WIKTIONARY_TEMPLATE
        ),
    )

    report = preview_file(source, mapping, source_root=tmp_path)

    assert [row["source_revision"] for row in report["rows"]] == ["6588944", ""], (
        "a revision cell is taken verbatim, and an empty cell stays empty: a word "
        "whose page does not exist upstream has no revision, and guessing one from "
        "the line number is exactly what must not happen"
    )
    assert report["rows"][1]["issues"] == [], (
        "an empty revision is legal input, not a row-level defect"
    )
    assert report["rows"][1]["normalized_word"] == "typist"

    # The declaration is frozen into the mapping verbatim, and all three keys are
    # always present so two identical declarations cannot hash differently.
    assert report["mapping"]["revision"] == {
        "column": "oldid", "value": "", "url_template": WIKTIONARY_TEMPLATE,
    }
    frozen = {
        "columns": {"word": "head", "meaning": "cn"},
        "required_fields": ["word"],
        "encoding": "utf-8",
        "delimiter": ",",
        "revision": report["mapping"]["revision"],
    }
    assert mapping_sha256(report["mapping"]) == mapping_sha256(frozen), (
        "the revision declaration must be covered by the mapping digest: it is part "
        "of what the artifact is, not a comment beside it"
    )
    assert mapping_sha256(report["mapping"]) != mapping_sha256(
        {key: value for key, value in frozen.items() if key != "revision"}
    ), "changing the declaration must change the digest of the source artifact"


@pytest.mark.parametrize("bad_revision", ["x" * 65, "has space", "tab\there", "bad\x7f"])
def test_invalid_row_revision_is_reported_without_changing_its_source_value(
    tmp_path: Path, bad_revision: str
) -> None:
    from app.services.public_lexicon_preview import (
        PreviewMapping,
        RevisionDeclaration,
        preview_file,
    )

    source = tmp_path / "invalid-revision.csv"
    source.write_text(f"head,oldid\nApple,{bad_revision}\n", encoding="utf-8")
    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "head"},
            revision=RevisionDeclaration(column="oldid", url_template=WIKTIONARY_TEMPLATE),
        ),
        source_root=tmp_path,
    )

    row = report["rows"][0]
    assert row["source_revision"] == bad_revision
    assert row["line"] == 2
    assert row["issues"] == [{"code": "invalid_source_revision", "field": "revision"}]
    assert row["normalized_word"] == ""
    assert report["summary"]["valid_rows"] == 0
    assert report["summary"]["error_rows"] == 1


def test_the_whole_file_can_carry_one_revision(tmp_path: Path) -> None:
    """The value form: a file pinned as a package or a commit, shared by every row."""
    from app.services.public_lexicon_preview import (
        PreviewMapping,
        RevisionDeclaration,
        preview_file,
    )

    commit = "70dc6b68c855f21e666a7a291ff8ead5ca1f7b44"
    source = tmp_path / "pinned-whole.csv"
    source.write_text("head\nabnormal\nzoom\n", encoding="utf-8")

    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "head"},
            revision=RevisionDeclaration(
                value=commit,
                url_template="https://github.com/exam-data/NETEMVocabulary/tree/{revision}",
            ),
        ),
        source_root=tmp_path,
    )

    assert [row["source_revision"] for row in report["rows"]] == [commit, commit]
    assert report["mapping"]["revision"]["column"] == ""
    assert report["mapping"]["revision"]["value"] == commit


def test_a_row_that_cannot_be_parsed_becomes_no_evidence_at_all(tmp_path: Path) -> None:
    """A malformed row cannot smuggle an empty revision into the evidence.

    Its revision stays empty in the report, but the row also has no normalized word
    and carries the issue that says why, so nothing downstream can adopt it.
    """
    from app.services.public_lexicon_preview import (
        PreviewMapping,
        RevisionDeclaration,
        preview_file,
    )

    source = tmp_path / "ragged.csv"
    source.write_text("head,oldid\nadmit,6588944\nby\n", encoding="utf-8")

    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "head"},
            encoding="utf-8",
            revision=RevisionDeclaration(column="oldid", url_template=WIKTIONARY_TEMPLATE),
        ),
        source_root=tmp_path,
    )

    ragged = report["rows"][1]
    assert ragged["source_revision"] == ""
    assert ragged["normalized_word"] == ""
    assert ragged["issues"] == [{"code": "column_count", "expected": 2, "actual": 1}]
    assert report["summary"]["error_rows"] == 1


def test_a_declared_revision_column_missing_from_the_header_is_an_error(
    tmp_path: Path,
) -> None:
    """Header drift must not look like "this source has no revisions".

    The two answer different questions: one is a fact a renderer degrades on, the
    other is a defect. Reporting the defect as the fact would record a blank that
    looks honest.
    """
    from app.services.public_lexicon_preview import (
        PreviewMapping,
        RevisionDeclaration,
        preview_file,
    )

    source = tmp_path / "drifted.csv"
    source.write_text("head,cn\nadmit,承认\n", encoding="utf-8")

    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "head", "meaning": "cn"},
            encoding="utf-8",
            revision=RevisionDeclaration(column="oldid", url_template=WIKTIONARY_TEMPLATE),
        ),
        source_root=tmp_path,
    )

    assert report["rows"] == []
    assert report["issues"] == [{"line": 1, "code": "missing_column", "field": "revision"}]
    assert report["summary"]["valid_rows"] == 0


def test_a_mapping_without_a_revision_is_bit_compatible_with_the_old_shape(
    tmp_path: Path,
) -> None:
    """Compatibility: a manifest that declares nothing produces the same bytes.

    ``preview_file``'s report is what ``mapping_sha256`` and the joint report digest
    are computed from, so a key added for every mapping would re-fingerprint every
    source already frozen. The key is therefore present only when a source declared
    something.
    """
    from app.services.public_lexicon_plan import mapping_sha256
    from app.services.public_lexicon_preview import PreviewMapping, preview_file

    source = tmp_path / "plain.csv"
    source.write_text("word,zh_meaning,zh_ipa\nadmit,承认,/ədˈmɪt/\n", encoding="utf-8")

    report = preview_file(
        source,
        PreviewMapping(
            columns={"word": "word", "meaning": "zh_meaning", "phonetic": "zh_ipa"},
            required_fields=("word",),
            encoding="utf-8",
        ),
        source_root=tmp_path,
    )

    assert set(report["mapping"]) == set(LEGACY_MAPPING) == {
        "columns", "required_fields", "encoding", "delimiter"
    }
    assert "revision" not in report["mapping"]
    assert mapping_sha256(report["mapping"]) == LEGACY_MAPPING_SHA256, (
        "a mapping that declares no revision must still hash to the digest every "
        "manifest written before the declaration produced"
    )
    assert "source_revision" not in report["rows"][0]


def test_the_declaration_refuses_the_shapes_that_would_leave_a_link_undefined() -> None:
    from app.services.public_lexicon_preview import RevisionDeclaration

    # Neither form: the revision is undefined rather than absent.
    with pytest.raises(ValueError, match="exactly one of column or value"):
        RevisionDeclaration(url_template=WIKTIONARY_TEMPLATE)
    # Both forms: which one wins would be an accident of read order.
    with pytest.raises(ValueError, match="exactly one of column or value"):
        RevisionDeclaration(column="oldid", value="1", url_template=WIKTIONARY_TEMPLATE)
    # A link is required: a revision a reader cannot reach answers nothing.
    with pytest.raises(ValueError, match="needs a url_template"):
        RevisionDeclaration(column="oldid")
    with pytest.raises(ValueError, match="needs a url_template"):
        RevisionDeclaration(column="oldid", url_template="   ")
    # Exactly one placeholder, and no other placeholder left verbatim.
    with pytest.raises(ValueError, match="exactly one \\{revision\\} placeholder, found 0"):
        RevisionDeclaration(column="oldid", url_template="https://example.org/page")
    with pytest.raises(ValueError, match="exactly one \\{revision\\} placeholder, found 2"):
        RevisionDeclaration(
            column="oldid", url_template="https://example.org/{revision}/{revision}"
        )
    with pytest.raises(ValueError, match="placeholder other than"):
        RevisionDeclaration(
            column="oldid", url_template="https://example.org/{page}?oldid={revision}"
        )
    # A link is offered to a reader, so it may not be relative or script-bearing.
    with pytest.raises(ValueError, match="must start with https://"):
        RevisionDeclaration(column="oldid", url_template="http://example.org/{revision}")
    with pytest.raises(ValueError, match="must start with https://"):
        RevisionDeclaration(column="oldid", url_template="javascript:alert('{revision}')")
    # An identifier is matched exactly, so whitespace is named rather than trimmed.
    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        RevisionDeclaration(column=" oldid", url_template=WIKTIONARY_TEMPLATE)
    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        RevisionDeclaration(value=" 1", url_template=WIKTIONARY_TEMPLATE)
    # Control characters, in either the identifier or the template.
    with pytest.raises(ValueError, match="control character"):
        RevisionDeclaration(column="old\tid", url_template=WIKTIONARY_TEMPLATE)
    with pytest.raises(ValueError, match="whitespace or a control character"):
        RevisionDeclaration(
            column="oldid", url_template="https://example.org/a b?oldid={revision}"
        )
    # Lengths: the identifier has a column to fit, the template a locator column.
    with pytest.raises(ValueError, match="longer than 64 characters"):
        RevisionDeclaration(column="c" * 65, url_template=WIKTIONARY_TEMPLATE)
    with pytest.raises(ValueError, match="longer than 400 characters"):
        RevisionDeclaration(
            column="oldid", url_template="https://example.org/" + "a" * 400 + "{revision}"
        )


@pytest.mark.parametrize("template", [
    "https://{revision}",
    "https://{revision}.example.org/page",
    "https://user:{revision}@example.org/page",
    "https://example.org:{revision}/page",
    "https:///{revision}",
])
def test_revision_template_requires_a_fixed_https_host(template: str) -> None:
    from app.services.public_lexicon_preview import RevisionDeclaration

    with pytest.raises(ValueError, match="fixed HTTPS host"):
        RevisionDeclaration(column="oldid", url_template=template)


def test_revision_template_puts_placeholder_only_in_path_or_query() -> None:
    from app.services.public_lexicon_preview import RevisionDeclaration

    with pytest.raises(ValueError, match="path or query"):
        RevisionDeclaration(
            column="oldid", url_template="https://example.org/page#{revision}"
        )
    assert RevisionDeclaration(
        column="oldid", url_template=WIKTIONARY_TEMPLATE
    ).url_template == WIKTIONARY_TEMPLATE
    github = "https://github.com/exam-data/NETEMVocabulary/tree/{revision}"
    assert RevisionDeclaration(column="oldid", url_template=github).url_template == github


def test_a_revision_column_cannot_also_be_a_canonical_field() -> None:
    """A source column is either lexicon content or revision metadata, not both.

    Mapping the revision column as a field too would import a revision identifier as
    a meaning while it is simultaneously the thing that locates the wording.
    """
    from app.services.public_lexicon_preview import (
        PreviewMapping,
        RevisionDeclaration,
    )

    with pytest.raises(ValueError, match="also mapped to a canonical field"):
        PreviewMapping(
            columns={"word": "head", "meaning": "oldid"},
            revision=RevisionDeclaration(column="oldid", url_template=WIKTIONARY_TEMPLATE),
        )
    with pytest.raises(ValueError, match="must be a RevisionDeclaration or None"):
        PreviewMapping(columns={"word": "head"}, revision={"column": "oldid"})
