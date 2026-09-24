"""Resource and path boundaries for local file preview."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.services.public_lexicon_preview import PreviewMapping, preview_file

MAPPING = PreviewMapping(columns={"word": "head", "meaning": "cn"})


def test_preview_rejects_file_above_byte_limit(tmp_path: Path, monkeypatch) -> None:
    from app.services import public_lexicon_preview as service

    root = tmp_path / "sources"
    root.mkdir()
    source = root / "large.csv"
    source.write_bytes(b"head,cn\n" + b"x" * 40)
    monkeypatch.setattr(service, "MAX_FILE_BYTES", 32)

    with pytest.raises(ValueError, match=r"file_too_large.*32"):
        preview_file(source, MAPPING, source_root=root)


def test_preview_rejects_too_many_physical_data_rows(tmp_path: Path, monkeypatch) -> None:
    from app.services import public_lexicon_preview as service

    source = tmp_path / "rows.csv"
    source.write_text("head,cn\na,one\nb,two\nc,three\n", encoding="utf-8")
    monkeypatch.setattr(service, "MAX_DATA_ROWS", 2)

    with pytest.raises(ValueError, match=r"too_many_rows.*2"):
        preview_file(source, MAPPING, source_root=tmp_path)


def test_preview_rejects_report_above_serialized_byte_limit(tmp_path: Path, monkeypatch) -> None:
    from app.services import public_lexicon_preview as service

    source = tmp_path / "report.csv"
    source.write_text("head,cn\na," + "中" * 50 + "\n", encoding="utf-8")
    monkeypatch.setattr(service, "MAX_REPORT_BYTES", 160)

    with pytest.raises(ValueError, match=r"report_too_large.*160"):
        preview_file(source, MAPPING, source_root=tmp_path)


def test_preview_rejects_parent_and_absolute_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("head,cn\na,one\n", encoding="utf-8")

    for path in (Path("../outside.csv"), outside):
        with pytest.raises(ValueError, match="outside_source_root"):
            preview_file(path, MAPPING, source_root=root)


def test_preview_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("head,cn\na,one\n", encoding="utf-8")
    link = root / "linked.csv"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="outside_source_root"):
        preview_file(link, MAPPING, source_root=root)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_preview_rejects_junction_escape_on_windows(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "entry.csv").write_text("head,cn\na,one\n", encoding="utf-8")
    link = root / "linked-directory"
    creation = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True, text=True, check=False,
    )
    assert creation.returncode == 0, creation.stdout + creation.stderr

    with pytest.raises(ValueError, match="outside_source_root"):
        preview_file(link / "entry.csv", MAPPING, source_root=root)


def test_cr_lf_are_physical_lines_but_u2028_stays_in_value(tmp_path: Path) -> None:
    source = tmp_path / "linebreaks.csv"
    source.write_bytes("head,cn\rfirst,one\u2028two\nsecond,three\r\n".encode())

    report = preview_file(source, MAPPING, source_root=tmp_path)

    assert report["summary"] == {
        "total_rows": 2, "valid_rows": 2, "duplicate_rows": 0, "error_rows": 0
    }
    assert [row["line"] for row in report["rows"]] == [2, 3]
    assert report["rows"][0]["values"]["meaning"] == "one\u2028two"


def test_decode_error_uses_cr_physical_line_number(tmp_path: Path) -> None:
    source = tmp_path / "bad-encoding.csv"
    source.write_bytes(b"head,cn\rfirst,ok\rsecond,\xff")

    report = preview_file(
        source, PreviewMapping(columns={"word": "head"}, encoding="utf-8"),
        source_root=tmp_path,
    )

    assert report["issues"][0]["code"] == "decode_error"
    assert report["issues"][0]["line"] == 3


def test_cli_requires_source_root_and_refuses_escape(tmp_path: Path, capsys) -> None:
    from app import cli

    root = tmp_path / "sources"
    root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("head,cn\na,one\n", encoding="utf-8")

    assert cli.main([
        "public-lexicon", "preview", str(outside), "--source-root", str(root),
        "--map", "word=head", "--map", "meaning=cn",
    ]) == 2
    assert "outside_source_root" in capsys.readouterr().err

    inside = root / "inside.csv"
    inside.write_text("head,cn\na,one\n", encoding="utf-8")
    assert cli.main([
        "public-lexicon", "preview", str(inside), "--source-root", str(root),
        "--map", "word=head", "--map", "meaning=cn",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["valid_rows"] == 1
