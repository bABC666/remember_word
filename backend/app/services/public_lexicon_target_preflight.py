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
    ready = [entry for entry in plan["entries"] if entry["status"] == "ready"]
    counts = {
        "new": 0, "matched": 0,
        "blocked": sum(entry["status"] == "blocked" for entry in plan["entries"]),
        "excluded": sum(entry["status"] == "excluded" for entry in plan["entries"]),
    }

    db_path = Path(database).resolve(strict=True)
    # mode=ro prevents writes even if a future caller accidentally issues DML.
    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if not _has_tables(connection):
            blockers.append("target_schema_missing: expected Phase 2.9 import tables")
        else:
            previous = connection.execute(
                "SELECT id, run_id FROM public_import_run WHERE plan_sha256 = ?",
                (plan["plan_sha256"],),
            ).fetchone()
            if previous is not None:
                prior_run = {"id": previous["id"], "run_id": previous["run_id"]}
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

            if target["exists"] and prior_run is None:
                for entry in ready:
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
        "plan_sha256": plan["plan_sha256"], "database": str(db_path),
        "target": target, "sources": sources, "entries": entries, "prior_run": prior_run,
        "counts": counts, "blockers": list(dict.fromkeys(blockers)),
        "ready_for_confirmation": not blockers,
        "notice": "机器预检只报告差异；授权真实性与释义选择仍须人工核实。",
    }


def _has_tables(connection: sqlite3.Connection) -> bool:
    found = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('lexicon', 'lexicon_entry', 'source_artifact', 'public_import_run')"
    )}
    return found == {"lexicon", "lexicon_entry", "source_artifact", "public_import_run"}


def _source_report(connection: sqlite3.Connection, source: dict[str, Any]) -> dict[str, Any]:
    provenance = source.get("provenance") or {}
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
        "missing_provenance": missing_provenance_fields(provenance),
        "source_artifact_id": row["id"] if row else None,
        "artifact_status": (
            "new" if row is None else "provenance_mismatch" if differences else "reused"
        ),
        "provenance_differences": differences,
    }
