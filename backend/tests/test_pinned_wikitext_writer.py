"""Validated writes from the locally preserved pinned-page archive only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.pinned_wikitext_writer import _paths, write_pinned_lines
from tests.test_source_wikitext_line import connect, migrated

ARCHIVE = (Path(__file__).resolve().parents[2] / "test-artifacts" /
           "phase29-provenance-evidence/kaoyan-vocab-research/rehearsal/pilot/zh-pinned-wikitext-300.json")
FILE_HASH = "09650bf2143440c5810b992a35a24ab8171094c6055aca111c02fe2a73a82a46"
SOURCE = "zhwiktionary-pinned-oldid"


def test_linked_english_heading_keeps_target_language_identity() -> None:
    language, path, _, _ = _paths(["==[[英语]]==", "===發音===", "#玩"])[2]
    assert language == "英语"
    assert path == "英语 > 發音"
PAGES = {
    "9576029": ("8b4e7331f2fe3cd59d3786cfdc0f85950243d4374b231febcd74ebc918768b68", (12, 15, 16, 19, 23)),
    "8457333": ("8f937bc8d81ea6f1a513efb17b45e357587dfac4610aec23b53d098cefe72d56", (3, 10, 12)),
    "7831922": ("042786853924911d423bb20f2df50ad822a7caf496eeaa98b99c88fa70c13d8a", (3, 6, 17, 20)),
}


def seed(db: Path, archive: Path = ARCHIVE, file_hash: str = FILE_HASH,
         mapping: str = "{}") -> int:
    with connect(db) as conn:
        conn.execute(
            "insert into source_artifact (role,name,publisher,version,obtained_at_utc,"
            "format,mapping_json,mapping_sha256,file_sha256,byte_size,license_id,"
            "license_text_sha256,use_scope,display_scope,storage_locator,created_at) "
            "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("meaning", archive.name, "test preserved archive", "pinned", "2026-01-01T00:00:00Z",
             "json", mapping, hashlib.sha256(mapping.encode()).hexdigest(), file_hash,
             archive.stat().st_size, "TEST", hashlib.sha256(b"TEST").hexdigest(),
             "test", "test", str(archive), "2026-01-01 00:00:00"),
        )
        return conn.execute("select last_insert_rowid()").fetchone()[0]


def expected(oldid: str, numbers: tuple[int, ...], archive: Path = ARCHIVE) -> dict[int, str]:
    page = json.loads(archive.read_bytes())[oldid]
    digest = hashlib.sha256(page.encode("utf-8")).hexdigest()
    return {n: hashlib.sha256("\n".join((SOURCE, oldid, digest, str(n),
            page.split("\n")[n-1])).encode("utf-8")).hexdigest()
            for n in numbers}


def write(db: Path, artifact_id: int, oldid: str, numbers: tuple[int, ...],
          *, archive: Path = ARCHIVE, file_hash: str = FILE_HASH,
          page_hash: str | None = None, line_hashes: dict[int, str] | None = None) -> int:
    with connect(db) as conn:
        return write_pinned_lines(
            conn, artifact_id=artifact_id, artifact_path=archive,
            expected_file_sha256=file_hash, source_id=SOURCE, oldid=oldid,
            expected_page_sha256=page_hash or PAGES[oldid][0],
            expected_line_sha256=line_hashes if line_hashes is not None else expected(oldid, numbers, archive),
        )


def rows(db: Path) -> list[tuple]:
    with connect(db) as conn:
        return [tuple(row) for row in conn.execute(
            "select page_revision,line_number,raw_text,language_path,heading_path,"
            "pos_heading_key,pos_heading_text,page_text_sha256,line_sha256 "
            "from source_wikitext_line order by page_revision,line_number")]


def test_three_pinned_pages_preserve_language_heading_and_hashes(tmp_path: Path) -> None:
    db = migrated(tmp_path)
    artifact_id = seed(db)
    for oldid, (_digest, numbers) in PAGES.items():
        assert write(db, artifact_id, oldid, numbers) == len(numbers)
        assert write(db, artifact_id, oldid, numbers) == 0
    by_position = {(r[0], r[1]): r for r in rows(db)}
    assert len(by_position) == 12
    assert by_position["9576029", 12][3:7] == ("英語", "英語", "adj", "形容詞")
    assert by_position["9576029", 15][3:7] == ("英語", "英語 > 形容詞", "", "")
    assert by_position["9576029", 23][3:7] == ("英語", "英語 > 副詞", "", "")
    assert by_position["8457333", 3][3:7] == ("英语", "英语", "", "")
    assert by_position["8457333", 10][3:7] == ("英语", "英语 > 發音", "", "")
    assert by_position["7831922", 20][3:7] == ("法語", "法語 > 動詞", "", "")
    for oldid, (page_hash, numbers) in PAGES.items():
        line_hashes = expected(oldid, numbers)
        for number in numbers:
            assert by_position[oldid, number][7:] == (page_hash, line_hashes[number])


@pytest.mark.parametrize("failure", ["file", "page", "line", "missing", "path", "position"])
def test_invalid_input_refuses_before_any_write(tmp_path: Path, failure: str) -> None:
    db = migrated(tmp_path)
    artifact_id = seed(db)
    archive = ARCHIVE
    oldid = "9576029"
    numbers = (12, 15)
    kwargs = {}
    if failure == "file":
        kwargs["file_hash"] = "0" * 64
    elif failure == "page":
        kwargs["page_hash"] = "0" * 64
    elif failure == "line":
        kwargs["line_hashes"] = {12: "0" * 64, 15: expected(oldid, numbers)[15]}
    elif failure == "missing":
        numbers = (12, 999)
        kwargs["line_hashes"] = {12: expected(oldid, (12,))[12], 999: "0" * 64}
    elif failure == "path":
        page = "unheaded text\n==英語==\n===名詞===\n# meaning"
        archive = tmp_path / "unheaded.json"
        archive.write_text(json.dumps({oldid: page}), encoding="utf-8")
        artifact_id = seed(db, archive, hashlib.sha256(archive.read_bytes()).hexdigest())
        numbers = (1, 4)
        kwargs = {"file_hash": hashlib.sha256(archive.read_bytes()).hexdigest(),
                  "page_hash": hashlib.sha256(page.encode()).hexdigest(),
                  "line_hashes": expected(oldid, numbers, archive)}
    else:
        assert write(db, artifact_id, oldid, (12,)) == 1
        pages = json.loads(ARCHIVE.read_bytes())
        pages[oldid] = pages[oldid].replace("===形容詞===", "===名詞===")
        archive = tmp_path / "changed.json"
        archive.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        file_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        second = seed(db, archive, file_hash)
        artifact_id = second
        numbers = (12, 15)
        kwargs = {"file_hash": file_hash,
                  "page_hash": hashlib.sha256(pages[oldid].encode()).hexdigest(),
                  "line_hashes": expected(oldid, numbers, archive)}
    with pytest.raises(ValueError):
        write(db, artifact_id, oldid, numbers, archive=archive, **kwargs)
    assert len(rows(db)) == (1 if failure == "position" else 0)


def test_unpreserved_oldid_is_refused(tmp_path: Path) -> None:
    db = migrated(tmp_path)
    artifact_id = seed(db)
    with connect(db) as conn, pytest.raises(ValueError, match="missing page"):
        write_pinned_lines(
            conn, artifact_id=artifact_id, artifact_path=ARCHIVE,
            expected_file_sha256=FILE_HASH, source_id=SOURCE, oldid="999999999",
            expected_page_sha256="0" * 64, expected_line_sha256={1: "0" * 64},
        )
    assert rows(db) == []
