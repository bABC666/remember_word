"""Write verified lines of an explicitly named, locally preserved wikitext archive.

The archive is a JSON object keyed by oldid (or an object with a ``pages`` key).
Callers must supply the expected file, page, and individual line fingerprints;
this module never fetches pages or infers a revision from a word.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from app.models import CONCISE_MEANING_POS_KEYS, source_wikitext_line_sha256

HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1$")
POS_HEADINGS = {
    "名詞": "noun", "名词": "noun", "動詞": "verb", "动词": "verb",
    "形容詞": "adj", "形容词": "adj", "副詞": "adv", "副词": "adv",
    "代詞": "pron", "代词": "pron", "限定詞": "det", "限定词": "det",
    "數詞": "num", "数词": "num", "介詞": "prep", "介词": "prep",
    "連詞": "conj", "连词": "conj", "感嘆詞": "interj", "感叹词": "interj",
    "助詞": "particle", "助词": "particle", "量詞": "classifier", "量词": "classifier",
    "縮寫": "abbrev", "缩写": "abbrev", "前綴": "prefix", "前缀": "prefix",
    "後綴": "suffix", "后缀": "suffix", "短語": "phrase", "短语": "phrase",
}
assert set(POS_HEADINGS.values()) <= set(CONCISE_MEANING_POS_KEYS)

FIELDS = (
    "source_artifact_id", "source_id", "page_revision", "line_number", "raw_text",
    "language_path", "heading_path", "pos_heading_key", "pos_heading_text",
    "page_text_sha256", "line_sha256",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _paths(lines: list[str]) -> list[tuple[str, str, str, str]]:
    stack: list[tuple[int, str]] = []
    result = []
    for line in lines:
        match = HEADING.fullmatch(line)
        if match:
            level, title = len(match.group(1)), match.group(2).strip()
            if not title or (level > 2 and not stack):
                raise ValueError("cannot determine heading path")
            stack = [(depth, text) for depth, text in stack if depth < level]
            if level > 2 and (not stack or stack[0][0] != 2):
                raise ValueError("cannot determine language path")
        language = next((text for depth, text in stack if depth == 2), "")
        path = " > ".join(text for _, text in stack)
        key = POS_HEADINGS.get(title, "") if match and level > 2 else ""
        result.append((language, path, key, title if key else ""))
        if match:
            stack.append((level, title))
    return result


def write_pinned_lines(
    connection: sqlite3.Connection, *, artifact_id: int, artifact_path: Path,
    expected_file_sha256: str, source_id: str, oldid: str,
    expected_page_sha256: str, expected_line_sha256: dict[int, str],
) -> int:
    """Validate an entire requested batch before inserting; return new row count.

    Existing identical positions are idempotent. Any conflicting position or
    fingerprint rejects the whole call, including rows that were otherwise new.
    The caller owns the connection and its transaction.
    """
    if (not oldid.isascii() or not oldid.isdecimal() or not source_id
            or source_id != source_id.strip() or ":" in source_id
            or "\n" in source_id or "\r" in source_id or not expected_line_sha256):
        raise ValueError("oldid, source_id and requested lines are required")
    if any(type(number) is not int for number in expected_line_sha256):
        raise ValueError("line numbers must be integers")
    artifact = connection.execute(
        "select file_sha256, byte_size, format from source_artifact where id=?",
        (artifact_id,),
    ).fetchone()
    if artifact is None or artifact[2] != "json":
        raise ValueError("missing preserved JSON artifact")
    try:
        file_bytes = Path(artifact_path).read_bytes()
    except OSError as exc:
        raise ValueError("missing preserved artifact file") from exc
    if (_sha(file_bytes) != expected_file_sha256 or artifact[0] != expected_file_sha256
            or artifact[1] != len(file_bytes)):
        raise ValueError("artifact byte fingerprint mismatch")
    try:
        archive = json.loads(file_bytes)
        pages = archive.get("pages", archive)
        page = pages[oldid]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError("missing page or invalid preserved archive") from exc
    if not isinstance(page, str) or _sha(page.encode("utf-8")) != expected_page_sha256:
        raise ValueError("page fingerprint mismatch")
    lines = page.split("\n")
    facts = _paths(lines)
    pending = []
    for number, expected_hash in sorted(expected_line_sha256.items()):
        if number < 1 or number > len(lines):
            raise ValueError(f"missing page line: {number}")
        raw = lines[number - 1]
        language, path, key, heading_text = facts[number - 1]
        if not raw.strip() or "\r" in raw or not language or not path:
            raise ValueError(f"cannot determine usable path at line {number}")
        line_hash = source_wikitext_line_sha256(
            source_id=source_id, page_revision=oldid, page_text_sha256=expected_page_sha256,
            line_number=number, raw_text=raw,
        )
        if line_hash != expected_hash:
            raise ValueError(f"line fingerprint mismatch at line {number}")
        values = (artifact_id, source_id, oldid, number, raw, language, path,
                  key, heading_text, expected_page_sha256, line_hash)
        existing = connection.execute(
            f"select {', '.join(FIELDS)} from source_wikitext_line "
            "where source_id=? and page_revision=? and line_number=?",
            (source_id, oldid, number),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values:
                raise ValueError(f"position conflict at {oldid}:{number}")
        else:
            pending.append(values)
    previous = connection.execute(
        "select distinct page_text_sha256 from source_wikitext_line "
        "where source_id=? and page_revision=?", (source_id, oldid),
    ).fetchall()
    if any(row[0] != expected_page_sha256 for row in previous):
        raise ValueError(f"page fingerprint conflict at {oldid}")
    if pending:
        connection.executemany(
            f"insert into source_wikitext_line ({', '.join(FIELDS)}, created_at) "
            f"values ({', '.join('?' for _ in FIELDS)}, CURRENT_TIMESTAMP)", pending,
        )
    return len(pending)
