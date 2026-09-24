"""Read-only preview of explicitly mapped, delimited vocabulary source files.

This module has no database dependency. It reports candidates and problems; a
separate, future confirmation path must decide whether anything can be written.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

FIELDS = frozenset({"word", "meaning", "phonetic", "part_of_speech"})
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_DATA_ROWS = 20_000
MAX_REPORT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class PreviewMapping:
    columns: dict[str, str]
    required_fields: tuple[str, ...] = ("word",)
    encoding: str = "utf-8-sig"
    delimiter: str = ","

    def __post_init__(self) -> None:
        if "word" not in self.columns or not self.columns["word"]:
            raise ValueError("word needs a source column")
        if not self.columns.keys() <= FIELDS:
            raise ValueError("unsupported canonical field")
        if any(not column for column in self.columns.values()):
            raise ValueError("source column names cannot be empty")
        if len(set(self.columns.values())) != len(self.columns):
            raise ValueError("one source column cannot map to multiple fields")
        if not set(self.required_fields) <= self.columns.keys() or "word" not in self.required_fields:
            raise ValueError("required fields must be mapped and include word")
        if len(self.delimiter) != 1 or self.delimiter in {'"', "\r", "\n"}:
            raise ValueError("delimiter must be one non-quote character")
        codecs.lookup(self.encoding)


def _fields(line: str, delimiter: str) -> list[str]:
    return next(csv.reader([line], delimiter=delimiter, strict=True))


def _bounded_report(report: dict[str, object]) -> dict[str, object]:
    size = len(json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    if size > MAX_REPORT_BYTES:
        raise ValueError(f"report_too_large: {size} bytes exceeds {MAX_REPORT_BYTES}")
    return report


def preview_file(
    path: Path, mapping: PreviewMapping, *, source_root: Path
) -> dict[str, object]:
    """Preview a file confined to source_root, without writing files or business rows."""
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("invalid_source_root: expected a directory")
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("outside_source_root: resolved file leaves the source directory")
    if not resolved.is_file():
        raise ValueError("invalid_source_file: expected a regular file")
    with resolved.open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"file_too_large: more than {MAX_FILE_BYTES} bytes")
    issues: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    summary = {"total_rows": 0, "valid_rows": 0, "duplicate_rows": 0, "error_rows": 0}
    report: dict[str, object] = {
        "file": {"name": resolved.name, "sha256": hashlib.sha256(raw).hexdigest(),
                 "byte_size": len(raw)},
        "mapping": {
            "columns": dict(mapping.columns), "required_fields": list(mapping.required_fields),
            "encoding": mapping.encoding, "delimiter": mapping.delimiter,
        },
        "summary": summary,
        "issues": issues,
        "rows": rows,
    }
    try:
        # CSV physical lines end only at CRLF, CR or LF. splitlines() also splits
        # U+2028/U+2029 and would invent rows and corrupt source line numbers.
        lines = re.split(r"\r\n|\r|\n", raw.decode(mapping.encoding))
    except UnicodeDecodeError as error:
        decoded_prefix = raw[:error.start].decode(mapping.encoding, errors="ignore")
        issues.append({
            "line": len(re.findall(r"\r\n|\r|\n", decoded_prefix)) + 1,
            "code": "decode_error",
            "byte_offset": error.start,
        })
        return _bounded_report(report)

    if lines and lines[-1] == "":
        lines.pop()

    if not lines:
        issues.append({"line": 1, "code": "empty_file"})
        return _bounded_report(report)
    summary["total_rows"] = len(lines) - 1
    if summary["total_rows"] > MAX_DATA_ROWS:
        raise ValueError(
            f"too_many_rows: {summary['total_rows']} exceeds {MAX_DATA_ROWS}"
        )
    try:
        header = _fields(lines[0], mapping.delimiter)
    except csv.Error:
        issues.append({"line": 1, "code": "csv_error"})
        return _bounded_report(report)
    if len(set(header)) != len(header):
        issues.append({"line": 1, "code": "duplicate_header"})
        return _bounded_report(report)
    for field, column in mapping.columns.items():
        if column not in header:
            issues.append({"line": 1, "code": "missing_column", "field": field})
    if issues:
        return _bounded_report(report)

    positions = {field: header.index(column) for field, column in mapping.columns.items()}
    seen: dict[str, int] = {}
    for line_number, raw_line in enumerate(lines[1:], start=2):
        row_issues: list[dict[str, object]] = []
        row: dict[str, object] = {
            "line": line_number, "raw": raw_line, "values": {},
            "normalized_word": "", "issues": row_issues,
        }
        rows.append(row)
        try:
            cells = _fields(raw_line, mapping.delimiter)
        except csv.Error:
            row_issues.append({"code": "csv_error"})
            summary["error_rows"] += 1
            continue
        if len(cells) != len(header):
            row_issues.append({"code": "column_count", "expected": len(header),
                               "actual": len(cells)})
            summary["error_rows"] += 1
            continue
        values = {field: cells[index] for field, index in positions.items()}
        row["values"] = values
        for field in mapping.required_fields:
            if not values[field].strip():
                row_issues.append({"code": "missing_value", "field": field})
        normalized = values["word"].strip().casefold()
        row["normalized_word"] = normalized
        if normalized:
            if normalized in seen:
                row_issues.append({"code": "duplicate_word", "field": "word",
                                   "first_line": seen[normalized]})
                summary["duplicate_rows"] += 1
            else:
                seen[normalized] = line_number
        if row_issues:
            summary["error_rows"] += 1
        else:
            summary["valid_rows"] += 1
    return _bounded_report(report)
