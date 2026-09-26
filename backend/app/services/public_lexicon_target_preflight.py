"""Read-only comparison of a locked public import plan with one SQLite target.

This is an advisory report. It cannot confirm authorization, adjudicate meanings,
or reserve rows against changes between preflight and confirmation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.services.public_lexicon_confirm import (
    ConfirmRefused,
    _reverify_evidence,
    _reverify_sources,
)
from app.services.public_lexicon_joint_preview import missing_provenance_fields
from app.services.public_lexicon_plan import PLAN_TYPE, plan_digest

PROVENANCE_FIELDS = (
    "publisher", "version", "obtained_at_utc", "license_id",
    "license_text_sha256", "use_scope", "display_scope", "storage_locator",
)
CONTENT_FIELDS = (
    "word", "phonetic", "part_of_speech", "source_meanings", "source_raw", "sequence",
)

#: Substrings by which a submitter marks a source as *not yet approved*. This is a
#: recogniser for a declaration, not a licence checker: the machine neither grants nor
#: judges authorisation. It is a fixed list, so it can only ever be a signal -- which
#: is why the report also says, unconditionally, that it did not assess authorisation.
#: The forms below are the ones a Chinese or English manifest actually uses for "not
#: approved" / "pending approval"; the earlier four-entry list missed ``未获批`` and a
#: hyphenated ``not-approved``, both of which read as an ordinary declaration.
UNAPPROVED_MARKERS = (
    "未获批准", "未获批", "未经批准", "未批准", "未获授权", "未经授权",
    "待批准", "待审批", "等待批准", "待负责人批准",
    "not approved", "not-approved", "notapprove", "unapproved", "not authorized",
    "not authorised", "pending approval", "pending owner approval", "awaiting approval",
)


def preflight_target(
    database: Path, *, plan: dict[str, Any], source_root: Path
) -> dict[str, Any]:
    """Compare frozen inputs with the target, using SQLite's read-only URI mode."""
    if not isinstance(plan, dict) or plan.get("plan_type") != PLAN_TYPE:
        raise ConfirmRefused("不是公共词库锁定计划。")
    if plan_digest(plan) != plan.get("plan_sha256"):
        raise ConfirmRefused("计划摘要与内容不符。")
    blockers = list(plan.get("confirmation_blockers") or [])
    if plan.get("confirmation_ready") is not True and not blockers:
        blockers.append("plan_not_ready")
    pending_sources = [
        source["source_id"] for source in plan["sources"]
        if _explicitly_unapproved(source.get("provenance") or {})
    ]
    blockers.extend(f"owner_approval_pending:{source_id}" for source_id in pending_sources)
    try:
        previews = _reverify_sources(plan, source_root=source_root)
        _reverify_evidence(previews, plan)
    except (ConfirmRefused, OSError, ValueError, KeyError) as error:
        blockers.append(str(error))

    name = (plan.get("target") or {}).get("lexicon", "")
    target: dict[str, Any] = {"exists": False, "id": None, "name": name}
    sources: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    prior_run: dict[str, Any] | None = None
    prior_run_other_target: dict[str, Any] | None = None
    ready = [entry for entry in plan["entries"] if entry["status"] == "ready"]
    counts = {
        "new": 0, "matched": 0,
        "blocked": sum(entry["status"] == "blocked" for entry in plan["entries"]),
        "excluded": sum(entry["status"] == "excluded" for entry in plan["entries"]),
    }

    db_path = Path(database).resolve(strict=True)
    read_mode, uri = _read_only_uri(db_path)
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if not _has_tables(connection):
            blockers.append("target_schema_missing: expected Phase 2.9 import tables")
        else:
            matches = connection.execute(
                "SELECT id FROM lexicon WHERE owner_user_id IS NULL "
                "AND visibility = 'public' AND name = ? ORDER BY id", (name.strip(),)
            ).fetchall()
            if len(matches) != 1:
                blockers.append(
                    "target_missing" if not matches else "target_ambiguous"
                )
            else:
                target.update(exists=True, id=matches[0]["id"])
            sources = [_source_report(connection, source) for source in plan["sources"]]
            for source in sources:
                if source["missing_provenance"]:
                    blockers.append(f"incomplete_provenance:{source['source_id']}")
                if source["artifact_status"] == "provenance_mismatch":
                    blockers.append(f"source_artifact_provenance_mismatch:{source['source_id']}")

            # The retry key is the plan digest, and the digest carries the target's
            # *name*, never its id. A run recorded against a different lexicon -- which
            # is what a rename plus a new lexicon under the old name leaves behind --
            # must not be reported as "already done here": the confirmation would then
            # answer ``already_applied`` and write nothing to the target in front of the
            # operator. It is reported on its own and it blocks.
            previous = connection.execute(
                "SELECT id, run_id, target_lexicon_id FROM public_import_run "
                "WHERE plan_sha256 = ? ORDER BY id",
                (plan["plan_sha256"],),
            ).fetchall()
            for row in previous:
                record = {
                    "id": row["id"], "run_id": row["run_id"],
                    "target_lexicon_id": row["target_lexicon_id"],
                }
                if target["exists"] and row["target_lexicon_id"] == target["id"]:
                    prior_run = record
                else:
                    prior_run_other_target = record
            if prior_run_other_target is not None:
                blockers.append("prior_run_target_mismatch")

            if target["exists"] and prior_run is None:
                for entry in ready:
                    if not isinstance(entry.get("default_snapshot"), dict):
                        # A digest-recomputed plan can carry a null snapshot; the diff
                        # cannot be computed, and that is a refusal, not a crash.
                        blockers.append(
                            f"malformed_snapshot:{entry['normalized_word']}"
                        )
                        continue
                    row = connection.execute(
                        "SELECT id, word, phonetic, part_of_speech, source_meanings, "
                        "source_raw, sequence FROM lexicon_entry "
                        "WHERE lexicon_id = ? AND normalized_word = ?",
                        (target["id"], entry["normalized_word"]),
                    ).fetchone()
                    if row is None:
                        counts["new"] += 1
                        entries.append({"normalized_word": entry["normalized_word"], "status": "new"})
                    else:
                        counts["matched"] += 1
                        planned = {**entry["default_snapshot"], "sequence": entry["sequence"]}
                        differences = {}
                        for field in CONTENT_FIELDS:
                            existing = json.loads(row[field]) if field == "source_meanings" else row[field]
                            if existing != planned[field]:
                                differences[field] = {"existing": existing, "planned": planned[field]}
                        entries.append({
                            "normalized_word": entry["normalized_word"], "status": "matched",
                            "lexicon_entry_id": row["id"], "content_differences": differences,
                        })

    if not target["exists"]:
        counts["blocked"] += len(ready)
    if blockers and target["exists"]:
        counts["blocked"] += counts["new"] + counts["matched"]
        counts["new"] = counts["matched"] = 0
    return {
        "authorization_review": {
            "status": "pending_owner_approval" if pending_sources else "not_assessed",
            "pending_sources": pending_sources,
            "message": (
                "来源声明明确写明未获批准；仍待负责人批准。机器预检不判断许可有效性。"
                if pending_sources else
                "来源元数据仅为提交者声明；机器预检未核实许可或负责人批准。"
            ),
        },
        "plan_sha256": plan["plan_sha256"], "database": str(db_path),
        "read_mode": read_mode,
        "target": target, "sources": sources, "entries": entries,
        "prior_run": prior_run,
        "prior_run_other_target": prior_run_other_target,
        "counts": counts, "blockers": list(dict.fromkeys(blockers)),
        "technical_preflight_passed": not blockers,
        "notice": "机器预检只报告差异；授权真实性与释义选择仍须人工核实。",
    }


def _read_only_uri(database: Path) -> tuple[str, str]:
    """How to open the target without changing anything next to it.

    ``mode=ro`` alone is not enough to call this read-only. Every database the
    application has opened carries ``journal_mode=WAL`` in its header (``app/db.py``
    sets it on connect), and SQLite still creates a ``-shm`` wal-index and a ``-wal``
    beside such a file when it opens it -- two new files in a directory the report
    describes as unchanged, and an outright failure on genuinely read-only media.

    ``immutable=1`` avoids both, but it also makes SQLite ignore a ``-wal`` that is
    present, which would answer from a stale snapshot: a database behind the code would
    compare as current. So it is used only when there is no live ``-wal`` to lose, and
    the live-WAL case keeps the plain read-only open. Which one was used is reported, so
    a reader never has to infer it.
    """
    wal = Path(str(database) + "-wal")
    live_wal = wal.exists() and wal.stat().st_size > 0
    if live_wal:
        return "read_only_with_live_wal", f"{database.as_uri()}?mode=ro"
    return "immutable", f"{database.as_uri()}?mode=ro&immutable=1"


def _has_tables(connection: sqlite3.Connection) -> bool:
    found = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('lexicon', 'lexicon_entry', 'source_artifact', 'public_import_run')"
    )}
    return found == {"lexicon", "lexicon_entry", "source_artifact", "public_import_run"}


def _source_report(connection: sqlite3.Connection, source: dict[str, Any]) -> dict[str, Any]:
    provenance = source.get("provenance") or {}
    explicitly_unapproved = _explicitly_unapproved(provenance)
    row = connection.execute(
        "SELECT id, publisher, version, obtained_at_utc, license_id, "
        "license_text_sha256, use_scope, display_scope, storage_locator "
        "FROM source_artifact WHERE file_sha256 = ? AND mapping_sha256 = ? AND role = ?",
        (source["file"]["sha256"], source["mapping_sha256"], source["role"]),
    ).fetchone()
    declared = {field: provenance.get(field, "") for field in PROVENANCE_FIELDS}
    declared["storage_locator"] = (
        declared["storage_locator"]
        or f"manifest:{source.get('declared_path', source['file']['name'])}"
    )
    differences = {}
    if row is not None:
        differences = {
            field: {"existing": row[field], "planned": value}
            for field, value in declared.items() if row[field] != value
        }
    return {
        "source_id": source["source_id"], "role": source["role"],
        "file_sha256": source["file"]["sha256"],
        "mapping_sha256": source["mapping_sha256"],
        "declared_provenance": declared,
        "declared_approval_state": (
            "explicitly_unapproved" if explicitly_unapproved else "not_assessed"
        ),
        "missing_provenance": missing_provenance_fields(provenance),
        "source_artifact_id": row["id"] if row else None,
        "artifact_status": (
            "new" if row is None else "provenance_mismatch" if differences else "reused"
        ),
        "provenance_differences": differences,
    }


def _explicitly_unapproved(provenance: dict[str, Any]) -> bool:
    """Recognize a submitter's explicit unapproved label, not legal validity.

    Case- and separator-insensitive: a manifest that writes ``not-approved`` or
    ``NOT APPROVED`` is making the same declaration as one that writes ``not
    approved``, and missing it would report a draft as an ordinary source. The report
    keeps saying it did not assess authorisation either way.
    """
    for value in provenance.values():
        text = str(value).casefold()
        flattened = text.replace("-", "").replace("_", "").replace(" ", "")
        for marker in UNAPPROVED_MARKERS:
            if marker in text or marker.replace("-", "") in flattened:
                return True
    return False
