"""Deterministic, read-only comparison of explicitly named vocabulary sources.

The result is evidence for a human review, not a plan that can be confirmed.
No database or source-license decision is made here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services.public_lexicon_preview import (
    FIELDS,
    REVISION_KEYS,
    PreviewMapping,
    RevisionDeclaration,
    preview_file,
)

FIELD_ORDER = ("word", "meaning", "phonetic", "part_of_speech")
MAX_SOURCES = 8
MAX_MANIFEST_BYTES = 256 * 1024
MAX_JOINT_REPORT_BYTES = 32 * 1024 * 1024

#: What a source must declare before its content may be written into the shared
#: lexicon. The design asks for the source's name, publisher, version, acquisition
#: time, licence, and the storage/processing/display scope it is allowed under, and
#: is explicit that these are **recorded, not verified**: a fingerprint cannot make a
#: licence claim true. What they must never be is empty. A row with a blank licence
#: is an import nobody can audit later, and the cheapest place to refuse it is before
#: it is written.
PROVENANCE_REQUIRED_FIELDS = (
    "publisher",
    "version",
    "obtained_at_utc",
    "license_id",
    "use_scope",
    "display_scope",
)

#: Optional, but validated when present. ``license_text_sha256`` fingerprints the
#: licence text when it ships as a file rather than a named licence; a manifest may
#: name a licence (``license_id``) without one.
PROVENANCE_OPTIONAL_FIELDS = ("license_text_sha256", "storage_locator")

PROVENANCE_FIELDS = PROVENANCE_REQUIRED_FIELDS + PROVENANCE_OPTIONAL_FIELDS

PROVENANCE_MAX_LENGTHS = {
    "publisher": 200,
    "version": 120,
    "obtained_at_utc": 40,
    "license_id": 80,
    "license_text_sha256": 64,
    "use_scope": 200,
    "display_scope": 200,
    "storage_locator": 400,
}


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    path: Path
    mapping: PreviewMapping
    #: The role this source is declared to play. The read-only comparison does not
    #: use it: a role only becomes binding in the locked plan, where the ``primary``
    #: source alone decides word membership. It stays optional here so manifests
    #: written before roles existed keep previewing; the plan loader requires it.
    role: str | None = None
    #: The declared provenance block, or None when the manifest omits it. The
    #: read-only comparison tolerates its absence -- evaluating a file before its
    #: licence is settled is exactly what a preview is for -- while the plan reports
    #: it as a blocker and confirmation refuses without it.
    provenance: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.source_id or self.source_id != self.source_id.strip():
            raise ValueError("invalid_source_id: use a nonempty stable source identifier")
        if len(self.source_id) > 80 or any(ord(char) < 32 for char in self.source_id):
            raise ValueError("invalid_source_id: identifier is too long or contains controls")
        if self.role is not None and (not self.role or self.role != self.role.strip()):
            raise ValueError("invalid_role: use a nonempty role name or omit it")


def parse_provenance(source_id: str, value: object) -> dict[str, str] | None:
    """Validate one manifest ``provenance`` block and normalise it.

    Shape is enforced here, completeness is not: an unknown key is refused rather
    than ignored (a typo like ``"license"`` would otherwise silently record nothing),
    timestamps must parse and carry a timezone, and a licence fingerprint must be a
    real SHA-256. Whether the required fields are *filled in* is the plan's decision,
    because that is the artifact a human reviews before confirming.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"invalid_manifest: source {source_id!r} provenance must be an object")
    unknown = sorted(set(value) - set(PROVENANCE_FIELDS))
    if unknown:
        raise ValueError(
            f"invalid_manifest: source {source_id!r} provenance has unknown field(s) "
            f"{unknown}; known fields are {list(PROVENANCE_FIELDS)}"
        )
    normalised: dict[str, str] = {}
    for field in PROVENANCE_FIELDS:
        raw = value.get(field, "")
        if not isinstance(raw, str):
            raise TypeError(
                f"invalid_manifest: source {source_id!r} provenance.{field} must be a string"
            )
        text = raw.strip()
        limit = PROVENANCE_MAX_LENGTHS[field]
        if len(text) > limit:
            raise ValueError(
                f"invalid_manifest: source {source_id!r} provenance.{field} exceeds {limit} characters"
            )
        if field == "obtained_at_utc" and text and _parse_instant(text) is None:
            raise ValueError(
                f"invalid_manifest: source {source_id!r} provenance.obtained_at_utc "
                "must be an ISO-8601 instant with a timezone, e.g. 2026-09-25T00:00:00Z"
            )
        if field == "license_text_sha256" and text and not _is_sha256(text):
            raise ValueError(
                f"invalid_manifest: source {source_id!r} provenance.license_text_sha256 "
                "must be 64 lowercase hex characters"
            )
        normalised[field] = text
    return normalised


def parse_revision(source_id: str, value: object) -> RevisionDeclaration | None:
    """Validate one manifest ``revision`` declaration and normalise it.

    ``None`` means the source declares no revision at all, which is an ordinary
    answer: a file pinned as a whole package may have no per-row revision to point at,
    and the design's response to that is no link rather than a guessed one.

    An unknown key is refused rather than ignored, for the same reason
    :func:`parse_provenance` refuses one -- a typo like ``"url"`` would otherwise
    record no template while the source still looks fully declared, and the failure
    would surface as a missing link much later.

    Unlike provenance, the declared strings are **not** stripped: a column name or a
    revision identifier is compared against file contents exactly, so quietly trimming
    one would hide the mismatch instead of naming it. :class:`RevisionDeclaration`
    refuses the whitespace with a message that says which field is at fault.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(
            f"invalid_manifest: source {source_id!r} revision must be an object"
        )
    unknown = sorted(set(value) - set(REVISION_KEYS))
    if unknown:
        raise ValueError(
            f"invalid_manifest: source {source_id!r} revision has unknown field(s) "
            f"{unknown}; known fields are {list(REVISION_KEYS)}"
        )
    declared: dict[str, str] = {}
    for field in REVISION_KEYS:
        raw = value.get(field, "")
        if not isinstance(raw, str):
            raise TypeError(
                f"invalid_manifest: source {source_id!r} revision.{field} must be a string"
            )
        declared[field] = raw
    try:
        return RevisionDeclaration(**declared)
    except ValueError as error:
        raise ValueError(
            f"invalid_manifest: source {source_id!r} revision: {error}"
        ) from error


def _parse_instant(text: str) -> datetime | None:
    # Python 3.11's fromisoformat accepts the trailing "Z" itself, so no rewriting.
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _is_sha256(text: str) -> bool:
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def missing_provenance_fields(provenance: dict[str, str] | None) -> list[str]:
    """The required provenance fields a source has not filled in.

    Sorted, and whitespace-only values count as missing, because the result goes into
    a blocker string that is part of the plan digest: it has to be stable across runs
    and it has to mean "declared" rather than "present as characters".
    """
    declared = provenance or {}
    return sorted(
        field for field in PROVENANCE_REQUIRED_FIELDS
        if not declared.get(field, "").strip()
    )


@dataclass(frozen=True)
class ManifestSpecs:
    """A parsed manifest plus the fingerprint of the bytes it was parsed from.

    The fingerprint matters because the plan is only reproducible when the exact
    manifest that produced it is known; ``sources`` carries the declared roles and
    provenance, which the read-only comparison ignores but the plan depends on.
    """

    sources: list[SourceSpec]
    required_fields: tuple[str, ...]
    file: dict[str, object]


def _json_bytes(value: object, *, canonical: bool = False) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=canonical,
        separators=(",", ":") if canonical else None,
        indent=None if canonical else 2,
    ).encode("utf-8")


def preview_sources(
    sources: Sequence[SourceSpec], *, source_root: Path,
    required_fields: tuple[str, ...] = ("meaning",),
) -> dict[str, object]:
    """Group parseable source rows by spelling while retaining each field's evidence."""
    if not sources or len(sources) > MAX_SOURCES:
        raise ValueError(f"source_count: expected 1 to {MAX_SOURCES} source files")
    ids = [source.source_id for source in sources]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_source_id: source identifiers must be unique")
    if not set(required_fields) <= FIELDS:
        raise ValueError("unsupported_required_field")

    source_reports: list[dict[str, object]] = []
    grouped: dict[str, dict[str, list[dict[str, object]]]] = {}
    for source in sorted(sources, key=lambda item: item.source_id):
        preview = preview_file(source.path, source.mapping, source_root=source_root)
        source_reports.append({"source_id": source.source_id, "preview": preview})
        file = preview["file"]
        for row in preview["rows"]:
            # A row can have a missing mapped value or duplicate-word issue and
            # still provide evidence for a recognizable spelling. Its issues
            # remain visible in the source report; only unparseable/blank words
            # cannot join a word group.
            if not row["normalized_word"]:
                continue
            word = row["normalized_word"]
            values = row["values"]
            fields = grouped.setdefault(word, {field: [] for field in FIELD_ORDER})
            for field in FIELD_ORDER:
                if field not in values:
                    continue
                fields[field].append({
                    "source_id": source.source_id,
                    "file_sha256": file["sha256"],
                    "line": row["line"],
                    "raw_line": row["raw"],
                    "raw_word": values["word"],
                    "raw_value": values[field],
                })

    entries: list[dict[str, object]] = []
    for word, fields in sorted(grouped.items()):
        for evidence in fields.values():
            evidence.sort(key=lambda item: (item["source_id"], item["line"], item["raw_value"]))
        missing = [field for field in FIELD_ORDER if field in required_fields
                   and not any(e["raw_value"].strip() for e in fields[field])]
        meanings = list(dict.fromkeys(
            e["raw_value"] for e in fields["meaning"] if e["raw_value"].strip()
        ))
        conflicts = ([{"field": "meaning", "raw_values": meanings}]
                     if len(meanings) > 1 else [])
        entries.append({
            "normalized_word": word,
            "fields": fields,
            "missing_fields": missing,
            "conflicts": conflicts,
        })

    report: dict[str, object] = {
        "normalization": "strip_casefold_v1",
        "sources": source_reports,
        "required_fields": [field for field in FIELD_ORDER if field in required_fields],
        "summary": {
            "source_files": len(source_reports),
            "candidate_words": len(entries),
            "meaning_conflicts": sum(bool(entry["conflicts"]) for entry in entries),
            "missing_field_words": sum(bool(entry["missing_fields"]) for entry in entries),
        },
        "entries": entries,
    }
    report["report_sha256"] = hashlib.sha256(_json_bytes(report, canonical=True)).hexdigest()
    size = len(_json_bytes(report))
    if size > MAX_JOINT_REPORT_BYTES:
        raise ValueError(f"joint_report_too_large: {size} bytes exceeds {MAX_JOINT_REPORT_BYTES}")
    return report


def load_manifest(path: Path, *, source_root: Path) -> ManifestSpecs:
    """Read a bounded local manifest under source_root and return its source specs.

    Split out of :func:`preview_manifest` because the locked plan needs the declared
    roles and the manifest fingerprint, while the read-only comparison needs only the
    resulting report. Both go through this one parser, so the two entry points can
    never disagree about what a manifest means.
    """
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("invalid_source_root: expected a directory")
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("outside_source_root: manifest leaves the source directory")
    if not resolved.is_file():
        raise ValueError("invalid_manifest: expected a regular file")
    with resolved.open("rb") as manifest_file:
        raw = manifest_file.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError(f"manifest_too_large: more than {MAX_MANIFEST_BYTES} bytes")
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), list):
        raise TypeError("invalid_manifest: sources must be a list")
    required = manifest.get("required_fields", ["meaning"])
    if not isinstance(required, list) or not all(isinstance(field, str) for field in required):
        raise ValueError("invalid_manifest: required_fields must be strings")
    specs: list[SourceSpec] = []
    for item in manifest["sources"]:
        if not isinstance(item, dict):
            raise TypeError("invalid_manifest: source must be an object")
        columns = item.get("columns")
        if not isinstance(columns, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in columns.items()
        ):
            raise ValueError("invalid_manifest: columns must map strings to strings")
        if not isinstance(item.get("id"), str) or not isinstance(item.get("file"), str):
            raise TypeError("invalid_manifest: id and file must be strings")
        role = item.get("role")
        if role is not None and not isinstance(role, str):
            raise TypeError("invalid_manifest: role must be a string")
        row_required = item.get("required_fields", ["word"])
        if not isinstance(row_required, list) or not all(
            isinstance(field, str) for field in row_required
        ):
            raise ValueError("invalid_manifest: source required_fields must be strings")
        specs.append(SourceSpec(
            source_id=item["id"], path=Path(item["file"]),
            mapping=PreviewMapping(
                columns=columns, required_fields=tuple(row_required),
                encoding=item.get("encoding", "utf-8-sig"),
                delimiter=item.get("delimiter", ","),
                revision=parse_revision(item["id"], item.get("revision")),
                sense_key_column=item.get("sense_key_column"),
            ),
            role=role,
            provenance=parse_provenance(item["id"], item.get("provenance")),
        ))
    return ManifestSpecs(
        sources=specs,
        required_fields=tuple(required),
        file={
            "name": resolved.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "byte_size": len(raw),
        },
    )


def preview_manifest(path: Path, *, source_root: Path) -> dict[str, object]:
    """Read a bounded local manifest under source_root, then preview its files."""
    specs = load_manifest(path, source_root=source_root)
    return preview_sources(
        specs.sources, source_root=source_root, required_fields=specs.required_fields
    )
