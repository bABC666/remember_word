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
from urllib.parse import urlsplit

FIELDS = frozenset({"word", "meaning", "phonetic", "part_of_speech"})
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_DATA_ROWS = 20_000
MAX_REPORT_BYTES = 16 * 1024 * 1024

#: The one placeholder a revision URL template may contain. Everything else in the
#: template is literal, and a second placeholder (or a different one) is refused
#: rather than left in the link verbatim.
REVISION_PLACEHOLDER = "{revision}"

#: ``entry_source_evidence.source_revision`` is ``String(64)``. A revision identifier
#: that cannot fit the column it is destined for is refused while the manifest is
#: being read, not later when a row is written.
REVISION_MAX_LENGTH = 64


def linkable_revision_identifier(text: str) -> bool:
    """Whether a nonempty revision can identify one fixed page in a URL."""
    return bool(text) and len(text) <= REVISION_MAX_LENGTH and not any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text
    )

#: The template is frozen into the mapping and becomes the link a reader follows to a
#: pinned revision. 400 characters matches the longest existing locator column
#: (``source_artifact.storage_locator``).
REVISION_URL_TEMPLATE_MAX_LENGTH = 400

#: The keys a revision declaration may carry, and the frozen shape they take.
REVISION_KEYS: tuple[str, ...] = ("column", "value", "url_template")


@dataclass(frozen=True)
class RevisionDeclaration:
    """Where a row's pinned revision comes from, and how to link to it.

    This is a **non-field** declaration: it sits beside ``columns`` rather than inside
    it, and ``FIELDS`` does not contain ``revision``. That separation is the point. A
    revision is metadata about where a value came from, not lexicon content, and a
    mapping that could name it as a column would let a revision identifier be imported
    and displayed as a meaning.

    Two mutually exclusive forms, because the two real sources differ in kind:

    * ``column`` -- the file carries one revision per row, and this names the column
      that holds it (zh.wiktionary pins one ``oldid`` per word);
    * ``value`` -- the whole file is one pinned revision (a package or a commit), so
      every row shares it.

    Both forms require ``url_template``: a revision a reader cannot reach does not
    answer "which version of the page did we use".

    Validation is deliberately strict about *identifiers*. A declaration is compared
    against file contents exactly, so leading or trailing whitespace is refused here
    with a message naming the field, rather than silently turning into a
    ``missing_column`` that sends someone looking for a header they did type.
    """

    column: str = ""
    value: str = ""
    url_template: str = ""

    def __post_init__(self) -> None:
        if bool(self.column) == bool(self.value):
            raise ValueError(
                "revision must declare exactly one of column or value: column names the "
                "source column that carries a revision per row, value pins one revision "
                "for the whole file. Neither, or both, leaves the revision undefined."
            )

        label = "column" if self.column else "value"
        declared = self.column or self.value
        if not declared.strip():
            raise ValueError(f"revision {label} cannot be blank")
        if declared != declared.strip():
            raise ValueError(
                f"revision {label} {declared!r} has leading or trailing whitespace; a "
                "declared identifier is matched and compared exactly, so stray "
                "whitespace would look for something that is not there"
            )
        if len(declared) > REVISION_MAX_LENGTH:
            raise ValueError(
                f"revision {label} is longer than {REVISION_MAX_LENGTH} characters "
                f"({len(declared)}): {declared[:20]!r}…"
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in declared):
            raise ValueError(f"revision {label} {declared!r} contains a control character")

        template = self.url_template
        if not template.strip():
            raise ValueError(
                "revision needs a url_template: a revision a reader cannot reach does "
                "not answer which version of the page a value came from"
            )
        if len(template) > REVISION_URL_TEMPLATE_MAX_LENGTH:
            raise ValueError(
                f"revision url_template is longer than "
                f"{REVISION_URL_TEMPLATE_MAX_LENGTH} characters ({len(template)})"
            )
        if not template.startswith("https://"):
            raise ValueError(
                f"revision url_template must start with https://, not {template[:16]!r}; "
                "a link is offered to a reader, so it may not be a relative or "
                "script-bearing URL"
            )
        if any(char.isspace() for char in template) or any(
            ord(char) < 32 or ord(char) == 127 for char in template
        ):
            raise ValueError(
                f"revision url_template {template!r} contains whitespace or a control "
                "character, which would be percent-encoded into a link nobody meant"
            )
        placeholders = template.count(REVISION_PLACEHOLDER)
        if placeholders != 1:
            raise ValueError(
                f"revision url_template must contain exactly one {REVISION_PLACEHOLDER} "
                f"placeholder, found {placeholders}"
            )
        remainder = template.replace(REVISION_PLACEHOLDER, "")
        if "{" in remainder or "}" in remainder:
            raise ValueError(
                f"revision url_template {template!r} contains a placeholder other than "
                f"{REVISION_PLACEHOLDER}, which would be left in the link verbatim"
            )
        try:
            parsed = urlsplit(template)
            host = parsed.hostname
            # Reading the port also rejects malformed fixed ports, including an
            # out-of-range number, before a reader is offered a misleading link.
            _ = parsed.port
        except ValueError as error:
            raise ValueError("revision url_template needs a fixed HTTPS host") from error
        if (
            not host or REVISION_PLACEHOLDER in parsed.netloc
            or "\\" in parsed.netloc
        ):
            raise ValueError("revision url_template needs a fixed HTTPS host")
        if REVISION_PLACEHOLDER not in parsed.path + parsed.query:
            raise ValueError("revision url_template must put {revision} in the path or query")

    def as_mapping(self) -> dict[str, str]:
        """The frozen form, as it enters the mapping digest and ``mapping_json``.

        All three keys are always present, with the unused one empty. The digest is
        computed from these bytes, so a shape that varied with which form was declared
        would make two identical declarations hash differently.
        """
        return {
            "column": self.column,
            "value": self.value,
            "url_template": self.url_template,
        }


@dataclass(frozen=True)
class PreviewMapping:
    columns: dict[str, str]
    required_fields: tuple[str, ...] = ("word",)
    encoding: str = "utf-8-sig"
    delimiter: str = ","
    #: The non-field revision declaration, or ``None`` when a source declares none.
    #: Absent is a legal, ordinary answer -- a source with no per-row revision simply
    #: gets no link -- and it is also what keeps every manifest written before this
    #: declaration existed working unchanged (see :func:`mapping_as_frozen`).
    revision: RevisionDeclaration | None = None
    # Optional original position within a dictionary or source snapshot. The
    # existing row locator remains the physical line of this import CSV.
    sense_key_column: str | None = None

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
        if self.revision is not None:
            if not isinstance(self.revision, RevisionDeclaration):
                raise ValueError("revision must be a RevisionDeclaration or None")
            if self.revision.column and self.revision.column in self.columns.values():
                raise ValueError(
                    f"revision column {self.revision.column!r} is also mapped to a "
                    "canonical field; a source column is either lexicon content or "
                    "revision metadata, not both"
                )
        if self.sense_key_column is not None and (
            not self.sense_key_column or self.sense_key_column in self.columns.values()
            or (self.revision is not None and self.sense_key_column == self.revision.column)
        ):
            raise ValueError("sense key must use its own nonempty source column")


def mapping_as_frozen(mapping: PreviewMapping) -> dict[str, object]:
    """The mapping exactly as it is fingerprinted and stored.

    Used both by the preview report and, through ``mapping_sha256``, as the identity
    of the source artifact. The ``revision`` key appears **only when a source declares
    one**: this dict is hashed and stored verbatim, so adding an empty key to every
    mapping would change the digest of every manifest written before the declaration
    existed -- and a changed mapping digest is a *different* source artifact, not a
    compatible one.
    """
    frozen: dict[str, object] = {
        "columns": dict(mapping.columns),
        "required_fields": list(mapping.required_fields),
        "encoding": mapping.encoding,
        "delimiter": mapping.delimiter,
    }
    if mapping.revision is not None:
        frozen["revision"] = mapping.revision.as_mapping()
    if mapping.sense_key_column is not None:
        frozen["sense_key_column"] = mapping.sense_key_column
    return frozen


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
        "mapping": mapping_as_frozen(mapping),
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
    # A declared revision column that the header does not carry is an error, not an
    # empty revision. The two must stay distinguishable: "this source has no
    # revision" is a fact a renderer degrades on, while "the header drifted" is a
    # defect that would otherwise be recorded as that same honest-looking blank.
    revision = mapping.revision
    if revision is not None and revision.column and revision.column not in header:
        issues.append({"line": 1, "code": "missing_column", "field": "revision"})
    if mapping.sense_key_column is not None and mapping.sense_key_column not in header:
        issues.append({"line": 1, "code": "missing_column", "field": "sense_key"})
    if issues:
        return _bounded_report(report)

    positions = {field: header.index(column) for field, column in mapping.columns.items()}
    revision_position = (
        header.index(revision.column) if revision is not None and revision.column else None
    )
    sense_key_position = (
        header.index(mapping.sense_key_column)
        if mapping.sense_key_column is not None else None
    )
    #: The revision every row shares when the file is pinned as a whole.
    fixed_revision = revision.value if revision is not None else ""
    seen: dict[str, int] = {}
    for line_number, raw_line in enumerate(lines[1:], start=2):
        row_issues: list[dict[str, object]] = []
        row: dict[str, object] = {
            "line": line_number, "raw": raw_line, "values": {},
            "normalized_word": "", "issues": row_issues,
        }
        if revision is not None:
            # Present only when the source declares one, so a manifest written before
            # this declaration produces byte-identical reports and therefore the same
            # ``report_sha256`` / ``mapping_sha256`` it always did. Reporting one key
            # for every row is what a reader downstream gets *whenever a revision was
            # declared*, in either form, so it never has to know which form it was.
            row["source_revision"] = fixed_revision
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
        if sense_key_position is not None:
            sense_key = cells[sense_key_position]
            row["sense_key"] = sense_key
            if (not sense_key or sense_key != sense_key.strip() or len(sense_key) > 80
                    or any(ord(char) < 32 or ord(char) == 127 for char in sense_key)):
                row_issues.append({"code": "invalid_sense_key", "field": "sense_key"})
        if revision_position is not None:
            # Taken verbatim, including an empty cell: the revision is what the source
            # says it is, and this is one of the values a re-read at confirmation has
            # to reproduce exactly. An empty cell is legal -- a word whose page does not
            # exist upstream has no revision -- and is never replaced by guessing one
            # from the line number.
            row["source_revision"] = cells[revision_position]
            if cells[revision_position] and not linkable_revision_identifier(
                cells[revision_position]
            ):
                row_issues.append({"code": "invalid_source_revision", "field": "revision"})
        for field in mapping.required_fields:
            if not values[field].strip():
                row_issues.append({"code": "missing_value", "field": field})
        normalized = values["word"].strip().casefold()
        invalid_metadata = any(
            issue["code"] in {"invalid_source_revision", "invalid_sense_key"}
            for issue in row_issues
        )
        row["normalized_word"] = "" if invalid_metadata else normalized
        if normalized and not invalid_metadata:
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
