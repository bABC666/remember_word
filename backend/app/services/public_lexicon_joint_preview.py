"""Deterministic, read-only comparison of explicitly named vocabulary sources.

The result is evidence for a human review, not a plan that can be confirmed.
No database or source-license decision is made here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.services.public_lexicon_preview import FIELDS, PreviewMapping, preview_file

FIELD_ORDER = ("word", "meaning", "phonetic", "part_of_speech")
MAX_SOURCES = 8
MAX_MANIFEST_BYTES = 256 * 1024
MAX_JOINT_REPORT_BYTES = 32 * 1024 * 1024


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

    def __post_init__(self) -> None:
        if not self.source_id or self.source_id != self.source_id.strip():
            raise ValueError("invalid_source_id: use a nonempty stable source identifier")
        if len(self.source_id) > 80 or any(ord(char) < 32 for char in self.source_id):
            raise ValueError("invalid_source_id: identifier is too long or contains controls")
        if self.role is not None and (not self.role or self.role != self.role.strip()):
            raise ValueError("invalid_role: use a nonempty role name or omit it")


@dataclass(frozen=True)
class ManifestSpecs:
    """A parsed manifest plus the fingerprint of the bytes it was parsed from.

    The fingerprint matters because the plan is only reproducible when the exact
    manifest that produced it is known; ``sources`` carries the declared roles,
    which the read-only comparison ignores but the plan depends on.
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
            ),
            role=role,
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
