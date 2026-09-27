"""Per-field source provenance for one entry, read from the database alone.

A displayed value is only checkable if a reader can get back to the source position
it came from. ``entry_source_evidence`` already records that position (``source_artifact``,
``row_locator``, ``field_kind``, ``raw_text``) and, since migration 0010, the pinned
revision of the page it was read at. This module is the read path that turns those
rows into a per-field block for one entry, and the only place that builds the
back-check link.

Four rules shape everything below.

**Nothing here reads a file.** The whole answer comes from the database, which is the
point of freezing the revision into the evidence row (design 3.2): the archive can be
moved away, or never delivered, and the block is byte-for-byte the same. A render path
that read the source would be a second way for the answer to be wrong.

**Only an adopted row may stand beside a displayed value.** ``confirm.py`` writes a row
for every source value the run *considered*, adopted or not, so the rows are split on
``selected_for_default``: ``selected`` is what the written content came from,
``candidates`` is everything a human saw and did not take. Collapsing the two would let
an unused source be shown as the origin of a displayed meaning (design 4, L2).

**A missing revision, or an unusable template, answers with an empty string.** The
link is only ever ``template.replace("{revision}", <quoted revision>)`` over a
``https://`` template the *frozen mapping* declared, and the template comes from the
evidence row's own artifact. An empty revision never falls back to a line number, a
template that is absent or malformed never produces a partial URL, and a revision that
is not an identifier (blank, over-long, whitespace, control characters) is not linked.
A wrong link is worse than no link: it sends a reader to a page that is not the one the
value came from and looks exactly like a right one.

**The block is display-safe by construction.** ``storage_locator`` (a local archive
path), ``mapping_json``, file and mapping digests, and the confirming operator's
identity stay out; ``raw_text`` is the one field value a source stated, which is what
makes the value checkable, not the file it was read from. Rows are selected by
``lexicon_entry_id`` alone, so another entry's evidence -- even one holding the same
word -- cannot appear.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EntrySourceEvidence, SourceArtifact
from app.services.public_lexicon_joint_preview import FIELD_ORDER
from app.services.public_lexicon_preview import (
    REVISION_MAX_LENGTH,
    REVISION_PLACEHOLDER,
    RevisionDeclaration,
)

#: Reported in ``completeness.missing`` when a degradation reason applies. Named
#: rather than counted, so a caller can branch on the machine-readable code and a
#: reader still gets the sentence that says which item is missing (design 4, L3).
NO_EVIDENCE = "no_evidence"
FIELD_WITHOUT_SELECTED_SOURCE = "field_without_selected_source"
SOURCE_REVISION_MISSING = "source_revision_missing"
SOURCE_REVISION_URL_UNAVAILABLE = "source_revision_url_unavailable"

#: What each reason means, said in the same words wherever it is reported.
_MESSAGES: dict[str, str] = {
    NO_EVIDENCE: (
        "该词条没有任何来源证据行（可能来自图片导入、旧数据，或尚未发生真实导入）"
    ),
    FIELD_WITHOUT_SELECTED_SOURCE: "字段「{field}」只有未采用的候选，没有采用来源",
    SOURCE_REVISION_MISSING: "字段「{field}」的采用来源没有记录固定修订号",
    SOURCE_REVISION_URL_UNAVAILABLE: (
        "字段「{field}」的采用来源无法合成固定修订链接（映射未声明修订或声明不可用）"
    ),
}


def frozen_revision_template(artifact: SourceArtifact) -> str:
    """The ``https://`` template this artifact's frozen mapping declares, or ``""``.

    Read from ``mapping_json``, which is part of what ``mapping_sha256`` covers, so
    the template cannot have changed under an existing evidence row. Validated by the
    same :class:`RevisionDeclaration` the manifest was accepted under: the read path
    and the write path must not disagree about what a usable link is, and a second
    copy of those rules is how they start to.

    A mapping that declares no revision is an ordinary answer, not an error -- every
    manifest written before the declaration existed has one, and a source with no
    pinned revision is exactly the case the design degrades on.
    """
    try:
        mapping = json.loads(artifact.mapping_json or "")
    except (TypeError, ValueError):
        return ""
    if not isinstance(mapping, dict):
        return ""
    declared = mapping.get("revision")
    if not isinstance(declared, dict):
        return ""
    values = {
        key: declared.get(key) for key in ("column", "value", "url_template")
    }
    if any(not isinstance(value, str) for value in values.values()):
        return ""
    try:
        return RevisionDeclaration(**values).url_template
    except (TypeError, ValueError):
        return ""


def revision_url(template: str, revision: str) -> str:
    """The link to the exact revision a value was read at, or ``""``.

    Empty is the answer whenever a link cannot be built honestly: no template, no
    revision, or a revision that is not an identifier. The revision is
    percent-encoded before it enters the URL, so a stored value containing ``?``,
    ``#``, ``&`` or ``/`` cannot reshape the link into something nobody meant.
    """
    text = revision or ""
    if not template or not text:
        return ""
    if len(text) > REVISION_MAX_LENGTH:
        return ""
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return template.replace(REVISION_PLACEHOLDER, quote(text, safe=""))


def _evidence_dict(
    row: EntrySourceEvidence, artifact: SourceArtifact, template: str
) -> dict[str, object]:
    """One evidence row as a client may see it.

    ``decision`` is the recorded word and ``selected_for_default`` is the flag the
    split is made on; both are reported so a reader is not asked to infer adoption
    from a string. ``source_revision_url`` is always present and is ``""`` when there
    is no link -- a missing key would make "no revision" and "not implemented" look
    alike.
    """
    revision = row.source_revision or ""
    return {
        "source_evidence_id": row.id,
        "field_kind": row.field_kind,
        "row_locator": row.row_locator,
        "sense_key": row.sense_key,
        "raw_text": row.raw_text,
        "decision": row.decision,
        "selected_for_default": bool(row.selected_for_default),
        "selection_order": row.selection_order,
        "source_revision": revision,
        "source_revision_url": revision_url(template, revision),
        # What the import recorded about the source: the declaration a human made,
        # never a local path and never the file itself.
        "source": {
            "source_artifact_id": artifact.id,
            "role": artifact.role,
            "name": artifact.name,
            "publisher": artifact.publisher,
            "version": artifact.version,
            "license_id": artifact.license_id,
        },
    }


def _rows(session: Session, entry_id: int) -> list[tuple[EntrySourceEvidence, SourceArtifact]]:
    """Every evidence row of **this** entry, with the artifact each one names.

    Scoped by ``lexicon_entry_id`` and by nothing else: not by word, because two
    entries may hold the same spelling and the word alone would mix their evidence.
    Ordered in SQL so the block is stable across requests, which is what makes "the
    same answer after the archive is gone" a meaningful comparison.
    """
    return list(
        session.execute(
            select(EntrySourceEvidence, SourceArtifact)
            .join(SourceArtifact, SourceArtifact.id == EntrySourceEvidence.source_artifact_id)
            .where(EntrySourceEvidence.lexicon_entry_id == entry_id)
            .order_by(
                EntrySourceEvidence.field_kind,
                EntrySourceEvidence.source_artifact_id,
                EntrySourceEvidence.row_locator,
                EntrySourceEvidence.id,
            )
        ).all()
    )


def _field_order(field_kinds: list[str]) -> list[str]:
    """The canonical field order first, any unexpected field after it.

    ``field_kind`` is written from the plan's own field list, so an unknown value
    means the table holds something this read path was not told about. Reporting it
    at the end keeps it visible instead of dropping it.
    """
    known = [field for field in FIELD_ORDER if field in field_kinds]
    extra = sorted(field for field in field_kinds if field not in FIELD_ORDER)
    return [*known, *extra]


def _missing(code: str, field_kind: str) -> dict[str, object]:
    return {
        "code": code,
        "field_kind": field_kind,
        "message": _MESSAGES[code].format(field=field_kind),
    }


def _completeness(
    fields: list[dict[str, object]], anything_recorded: bool
) -> dict[str, object]:
    """Say, in the payload, which item of the source record is missing.

    A reader who is shown a source must be able to tell a complete record from a
    partial one; silently omitting the block, or showing a link for the part that
    happens to be present, would make the two look alike (design 4, L3).
    """
    missing: list[dict[str, object]] = []
    if not anything_recorded:
        missing.append(_missing(NO_EVIDENCE, ""))
    for field in fields:
        selected = field["selected"]
        field_kind = str(field["field_kind"])
        if not selected:
            missing.append(_missing(FIELD_WITHOUT_SELECTED_SOURCE, field_kind))
            continue
        for item in selected:
            if not item["source_revision"]:
                missing.append(_missing(SOURCE_REVISION_MISSING, field_kind))
            elif not item["source_revision_url"]:
                missing.append(_missing(SOURCE_REVISION_URL_UNAVAILABLE, field_kind))
    if not missing:
        return {"status": "complete", "missing": [], "message": ""}
    return {
        "status": "incomplete",
        "missing": missing,
        "message": "该词条的来源记录不完整："
        + "；".join(str(item["message"]) for item in missing)
        + "。",
    }


def entry_sources(session: Session, entry_id: int) -> dict[str, object]:
    """The per-field source record of one entry, adopted and candidate rows apart.

    One query for the whole block, so serving a word detail costs one statement. A
    word with no source record at all -- an image import, an entry from before any
    real import, or simply one that has not happened yet -- answers an empty
    ``fields`` list with a ``completeness`` block naming that fact, rather than an
    error or a block that a client would have to guess the meaning of.
    """
    pairs = _rows(session, entry_id)
    templates: dict[int, str] = {}
    grouped: dict[str, dict[str, list[dict[str, object]]]] = {}
    for row, artifact in pairs:
        if artifact.id not in templates:
            templates[artifact.id] = frozen_revision_template(artifact)
        item = _evidence_dict(row, artifact, templates[artifact.id])
        bucket = grouped.setdefault(row.field_kind, {"selected": [], "candidates": []})
        bucket["selected" if row.selected_for_default else "candidates"].append(item)

    fields = [
        {
            "field_kind": field_kind,
            "selected": grouped[field_kind]["selected"],
            "candidates": grouped[field_kind]["candidates"],
        }
        for field_kind in _field_order(list(grouped))
    ]
    return {"fields": fields, "completeness": _completeness(fields, bool(pairs))}
