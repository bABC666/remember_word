"""Administrator confirmation of a locked public-lexicon plan.

This is the write half of Phase 2.9. It takes a plan produced by
``app.services.public_lexicon_plan``, re-proves every input it depends on, and then
records the import in one transaction.

What it does, in order:

1. refuses unless the caller is an active administrator;
2. re-checks the plan's own digest, so an edited plan file is not a plan;
3. refuses a plan whose ``confirmation_ready`` is false -- an unadjudicated conflict
   or a missing required field is a stop, not a warning;
4. **returns the original result if this exact plan was already confirmed**, before
   touching the source files, so a retry after the files moved still reports what
   happened the first time;
5. re-reads every source file, re-computes its SHA-256 and mapping fingerprint, and
   compares the values it finds against the plan. Any difference refuses the plan
   and asks for a fresh preview;
6. writes ``source_artifact``, ``public_import_run``, ``public_import_run_source``,
   ``entry_source_evidence`` and any new ``lexicon_entry`` rows **in a single
   transaction**; a failure rolls all of it back.

Three things it deliberately never does:

* it never updates an existing ``lexicon_entry``. A word already in the target
  lexicon is reported as a conflict and left exactly as it was, so an import cannot
  overwrite a definition a user is already studying;
* it never creates ``UserWordState`` or ``ReviewEvent``. Claiming new words is a
  separate path (design section 5); confirming writes content only;
* it never issues an UPDATE or DELETE against the provenance tables, and never
  writes ``lexicon.entry_count``. Evidence is append-only: re-adjudicating the same
  source value in a later run appends a row carrying that run's identity instead of
  rewriting the earlier decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    EntrySourceEvidence,
    Lexicon,
    LexiconEntry,
    PublicImportRun,
    PublicImportRunSource,
    SourceArtifact,
    User,
)
from app.services.public_lexicon_joint_preview import (
    FIELD_ORDER,
    missing_provenance_fields,
)
from app.services.public_lexicon_plan import (
    PLAN_TYPE,
    canonical_bytes,
    mapping_sha256,
    plan_digest,
)
from app.services.public_lexicon_preview import PreviewMapping, preview_file

STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED = "already_applied"

DECISION_SELECTED = "selected"
DECISION_NOT_SELECTED = "not_selected"

#: The one format this slice can re-read. Recorded per artifact so a later reader
#: can tell which reader produced a row's evidence.
ARTIFACT_FORMAT = "delimited-text-v1"


class ConfirmRefused(Exception):
    """The plan cannot be confirmed as it stands. **Nothing was written.**

    Every refusal raised before the write transaction leaves the database exactly as
    it was. The message says what to fix: the answer is a fresh preview and a fresh
    adjudication, never a hand-edited plan.
    """


def confirm_plan(
    session: Session,
    *,
    plan: dict[str, Any],
    administrator: User,
    source_root: Path,
    confirmed_at: datetime | None = None,
) -> dict[str, Any]:
    """Re-prove a locked plan, then record it -- or refuse and write nothing."""
    moment = _require_aware(confirmed_at or datetime.now(UTC))
    _require_administrator(administrator)
    _require_intact_plan(plan)

    lexicon = _target_lexicon(session, plan)

    existing = session.scalar(
        select(PublicImportRun).where(PublicImportRun.plan_sha256 == plan["plan_sha256"])
    )
    if existing is not None:
        # Deliberately checked before the source files are re-read: a retry has to
        # report what the first confirmation did even if the files have since moved.
        return {
            "status": STATUS_ALREADY_APPLIED,
            "run_id": existing.run_id,
            "import_run_id": existing.id,
            "plan_sha256": existing.plan_sha256,
            "entries_created": existing.entries_created,
            "entries_matched": existing.entries_matched,
            "evidence_written": existing.evidence_written,
            "conflicts": list(existing.result_json.get("conflicts", [])),
            "detail": "该计划此前已确认；未写入任何新内容。",
        }

    previews = _reverify_sources(plan, source_root=source_root)

    try:
        result = _write(
            session,
            plan=plan,
            lexicon=lexicon,
            administrator=administrator,
            previews=previews,
            moment=moment,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return result


# --- refusals, all of which happen before anything is written ----------------


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("confirmed_at must be timezone-aware")
    return moment.astimezone(UTC)


def _require_administrator(administrator: User | None) -> None:
    """Only an active administrator may confirm.

    The caller verifies the administrator's own current password; this is the
    capability half of the check, and deliberately not sufficient on its own.
    """
    if administrator is None:
        raise ConfirmRefused("需要管理员身份才能确认公共词库导入。")
    if not administrator.is_active:
        raise ConfirmRefused("该管理员账号已停用，拒绝确认。")
    if not administrator.is_admin:
        raise ConfirmRefused("当前账号不是管理员，拒绝确认。")


def _require_intact_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict) or plan.get("plan_type") != PLAN_TYPE:
        raise ConfirmRefused("不是公共词库锁定计划，拒绝确认。")
    if plan_digest(plan) != plan.get("plan_sha256"):
        raise ConfirmRefused("计划摘要与内容不符（文件可能被编辑过），拒绝确认。")
    if plan.get("confirmation_ready") is not True:
        blockers = "；".join(plan.get("confirmation_blockers") or []) or "未知"
        raise ConfirmRefused(f"计划尚未就绪，仍存在阻断项：{blockers}")


def _target_lexicon(session: Session, plan: dict[str, Any]) -> Lexicon:
    """Resolve the declared target, requiring exactly one system public lexicon.

    The plan carries only a name: the read-only slice never opened a database, so it
    could not record an id. Matching happens here, and an ambiguous name is refused
    rather than guessed.
    """
    name = (plan.get("target") or {}).get("lexicon")
    if not isinstance(name, str) or not name.strip():
        raise ConfirmRefused("计划未声明目标公共词库名称。")
    matches = session.scalars(
        select(Lexicon).where(
            Lexicon.owner_user_id.is_(None),
            Lexicon.visibility == "public",
            Lexicon.name == name.strip(),
        )
    ).all()
    if not matches:
        raise ConfirmRefused(f"找不到名为 {name!r} 的系统公共词库。")
    if len(matches) > 1:
        raise ConfirmRefused(
            f"有 {len(matches)} 个系统公共词库都叫 {name!r}，无法确定目标，拒绝确认。"
        )
    return matches[0]


# --- re-proving the inputs ---------------------------------------------------


def _reverify_sources(
    plan: dict[str, Any], *, source_root: Path
) -> dict[str, dict[str, Any]]:
    """Re-read every source and prove it still matches what the plan froze.

    A plan is only meaningful while the bytes it was built from are unchanged, so
    this is a refusal gate rather than a repair step: a difference means the operator
    previews again, not that this run adapts.
    """
    root = source_root.resolve(strict=True)
    previews: dict[str, dict[str, Any]] = {}
    for source in plan["sources"]:
        source_id = source["source_id"]
        declared = source.get("declared_path")
        if not isinstance(declared, str) or not declared:
            raise ConfirmRefused(
                f"来源 {source_id!r} 未记录清单内的相对路径，无法重新读取；请重新预览。"
            )
        try:
            preview = preview_file(
                Path(declared), _mapping_from_plan(source), source_root=root
            )
        except (OSError, ValueError) as error:
            raise ConfirmRefused(f"来源 {source_id!r} 无法重新读取：{error}") from error
        frozen = source["file"]
        if preview["file"]["sha256"] != frozen["sha256"]:
            raise ConfirmRefused(
                f"来源 {source_id!r} 的文件内容已变化"
                f"（计划 {frozen['sha256'][:12]}…，现在 "
                f"{preview['file']['sha256'][:12]}…），请重新预览并重新裁定。"
            )
        if mapping_sha256(preview["mapping"]) != source["mapping_sha256"]:
            raise ConfirmRefused(f"来源 {source_id!r} 的字段映射已变化，请重新预览。")
        previews[source_id] = preview
    return previews


def _mapping_from_plan(source: dict[str, Any]) -> PreviewMapping:
    frozen = source["mapping"]
    return PreviewMapping(
        columns=dict(frozen["columns"]),
        required_fields=tuple(frozen["required_fields"]),
        encoding=frozen["encoding"],
        delimiter=frozen["delimiter"],
    )


def _reverify_evidence(
    previews: dict[str, dict[str, Any]], plan: dict[str, Any]
) -> None:
    """Compare the values the plan recorded against the values the files still hold.

    The file fingerprint already covers this, but re-deriving by locator is cheap and
    turns "the file is unchanged" into "the specific value this plan would write is
    still there", which is what the confirmation actually claims.
    """
    for entry in plan["entries"]:
        if entry["status"] != "ready":
            continue
        for field in FIELD_ORDER:
            for item in entry["evidence"][field]:
                current = _value_at(previews[item["source_id"]], item["line"], field)
                if current != item["raw_value"]:
                    raise ConfirmRefused(
                        f"{entry['normalized_word']!r} 的 {field} 证据在 "
                        f"{item['source_id']} 第 {item['line']} 行已与计划不符，"
                        "请重新预览并重新裁定。"
                    )


def _value_at(preview: dict[str, Any], line: int, field: str) -> str | None:
    for row in preview["rows"]:
        if row["line"] == line:
            return row["values"].get(field)
    return None


# --- the write transaction ---------------------------------------------------


def _write(
    session: Session,
    *,
    plan: dict[str, Any],
    lexicon: Lexicon,
    administrator: User,
    previews: dict[str, dict[str, Any]],
    moment: datetime,
) -> dict[str, Any]:
    _reverify_evidence(previews, plan)

    run = PublicImportRun(
        plan_sha256=plan["plan_sha256"],
        run_id=plan["run_id"],
        target_lexicon_id=lexicon.id,
        confirmed_by_user_id=administrator.id,
        confirmed_by_username=administrator.username,
        confirmed_at=moment,
        status=STATUS_APPLIED,
    )
    session.add(run)
    session.flush()

    artifacts: dict[str, SourceArtifact] = {}
    sources_created = 0
    for source in plan["sources"]:
        artifact, created = _get_or_create_artifact(session, source, moment)
        artifacts[source["source_id"]] = artifact
        sources_created += int(created)
        session.add(PublicImportRunSource(
            import_run_id=run.id,
            source_artifact_id=artifact.id,
            outcome="created" if created else "reused",
            detail=f"role={source['role']} file={source['file']['name']}",
        ))

    created_entries: list[tuple[dict[str, Any], LexiconEntry]] = []
    conflicts: list[dict[str, Any]] = []
    for entry in plan["entries"]:
        if entry["status"] != "ready":
            continue
        existing = session.scalar(
            select(LexiconEntry).where(
                LexiconEntry.lexicon_id == lexicon.id,
                LexiconEntry.normalized_word == entry["normalized_word"],
            )
        )
        if existing is not None:
            # Never overwrite. A word already in the shared lexicon may be in
            # someone's study plan, and the meanings shown for it are already in use.
            conflicts.append({
                "normalized_word": entry["normalized_word"],
                "reason": "already_in_lexicon",
                "lexicon_entry_id": existing.id,
            })
            continue
        created_entries.append((entry, _create_entry(session, lexicon, entry)))

    evidence = _record_evidence(
        session,
        plan=plan,
        run_id=run.id,
        artifacts=artifacts,
        created_entries=created_entries,
        administrator=administrator,
        moment=moment,
    )

    run.entries_created = len(created_entries)
    run.entries_matched = len(conflicts)
    run.evidence_written = evidence["written"]
    run.result_json = {
        "sources_created": sources_created,
        "sources_reused": len(plan["sources"]) - sources_created,
        "conflicts": conflicts,
        "evidence": evidence,
    }
    session.flush()
    return {
        "status": STATUS_APPLIED,
        "run_id": run.run_id,
        "import_run_id": run.id,
        "plan_sha256": plan["plan_sha256"],
        "target_lexicon": {"id": lexicon.id, "name": lexicon.name},
        "entries_created": run.entries_created,
        "entries_matched": run.entries_matched,
        "evidence_written": run.evidence_written,
        "sources_created": sources_created,
        "conflicts": conflicts,
        "evidence": evidence,
    }


def _get_or_create_artifact(
    session: Session, source: dict[str, Any], moment: datetime
) -> tuple[SourceArtifact, bool]:
    """One artifact per (file bytes, mapping, role); a later run reuses it.

    The provenance comes from the plan's frozen block, and a blank required field
    refuses the whole confirmation. That redundancy is deliberate: the plan already
    blocks on incomplete provenance and the table has a CHECK constraint, and this is
    the layer that says *why* in words an operator can act on. A row with an empty
    licence is an import nobody can audit later, so no path should be able to create
    one -- including a plan whose digest someone recomputed by hand.
    """
    provenance = source.get("provenance") or {}
    missing = missing_provenance_fields(provenance)
    if missing:
        raise ConfirmRefused(
            f"来源 {source['source_id']!r} 的授权元数据不完整（缺少 "
            + "、".join(missing)
            + "），拒绝写入。请在清单里补齐来源的发布者、版本、取得时间、许可、"
            "使用范围与展示范围后重新预览并重新裁定。"
        )
    found = session.scalar(
        select(SourceArtifact).where(
            SourceArtifact.file_sha256 == source["file"]["sha256"],
            SourceArtifact.mapping_sha256 == source["mapping_sha256"],
            SourceArtifact.role == source["role"],
        )
    )
    if found is not None:
        declared = {
            "publisher": provenance["publisher"],
            "version": provenance["version"],
            "obtained_at_utc": provenance["obtained_at_utc"],
            "license_id": provenance["license_id"],
            "license_text_sha256": provenance.get("license_text_sha256", ""),
            "use_scope": provenance["use_scope"],
            "display_scope": provenance["display_scope"],
            "storage_locator": (
                provenance.get("storage_locator")
                or f"manifest:{source.get('declared_path', source['file']['name'])}"
            ),
        }
        if any(getattr(found, field) != value for field, value in declared.items()):
            raise ConfirmRefused(
                f"来源 {source['source_id']!r} 与已归档文件的授权元数据不一致；"
                "拒绝复用旧来源记录。请先核实声明与归档记录。"
            )
        return found, False
    artifact = SourceArtifact(
        role=source["role"],
        name=source["file"]["name"],
        publisher=provenance["publisher"],
        version=provenance["version"],
        obtained_at_utc=provenance["obtained_at_utc"],
        format=ARTIFACT_FORMAT,
        mapping_json=canonical_bytes(source["mapping"]).decode("utf-8"),
        mapping_sha256=source["mapping_sha256"],
        file_sha256=source["file"]["sha256"],
        byte_size=int(source["file"]["byte_size"]),
        license_id=provenance["license_id"],
        license_text_sha256=provenance.get("license_text_sha256", ""),
        use_scope=provenance["use_scope"],
        display_scope=provenance["display_scope"],
        storage_locator=(
            provenance.get("storage_locator")
            or f"manifest:{source.get('declared_path', source['file']['name'])}"
        ),
        created_at=moment,
    )
    session.add(artifact)
    session.flush()
    return artifact, True


def _create_entry(
    session: Session, lexicon: Lexicon, entry: dict[str, Any]
) -> LexiconEntry:
    """Write the adjudicated default snapshot onto a new entry.

    Only what the plan's ``default_snapshot`` carries is written. ``default_anchor``
    stays empty (an AI product, a different path), ``frequency_*`` stays NULL (no
    verified frequency data exists), and ``lexicon.entry_count`` is not touched.
    """
    snapshot = entry["default_snapshot"]
    created = LexiconEntry(
        lexicon_id=lexicon.id,
        word=snapshot["word"],
        normalized_word=entry["normalized_word"],
        phonetic=snapshot["phonetic"],
        part_of_speech=snapshot["part_of_speech"],
        source_meanings=list(snapshot["source_meanings"]),
        source_raw=snapshot["source_raw"],
        sequence=entry["sequence"],
    )
    session.add(created)
    session.flush()
    return created


def _record_evidence(
    session: Session,
    *,
    plan: dict[str, Any],
    run_id: int,
    artifacts: dict[str, SourceArtifact],
    created_entries: list[tuple[dict[str, Any], LexiconEntry]],
    administrator: User,
    moment: datetime,
) -> dict[str, int]:
    """Append this run's adjudication of each source value, never rewriting history.

    One row per field value the run actually considered. An identical row from an
    earlier run is not duplicated; a *different* decision on the same source position
    appends a new row rather than editing the old one, so the earlier decision stays
    readable. Nothing here issues an UPDATE or a DELETE.
    """
    counters = {"written": 0, "skipped_existing": 0, "readjudicated": 0}
    for entry, created in created_entries:
        selected = {
            field: [
                (item["source_id"], item["line"])
                for item in entry["default_evidence"].get(field, [])
            ]
            for field in FIELD_ORDER
        }
        raw_words = {
            (item["source_id"], item["line"]): item["raw_value"]
            for item in entry["evidence"]["word"]
        }
        for field in FIELD_ORDER:
            for item in entry["evidence"][field]:
                locator = (item["source_id"], item["line"])
                is_selected = locator in selected[field]
                position = selected[field].index(locator) if is_selected else None
                decision = DECISION_SELECTED if is_selected else DECISION_NOT_SELECTED
                prior = session.scalars(
                    select(EntrySourceEvidence)
                    .where(EntrySourceEvidence.evidence_sha256 == item["idempotency_key"])
                    .order_by(EntrySourceEvidence.id.desc())
                ).first()
                if prior is not None:
                    unchanged = (
                        prior.decision == decision
                        and bool(prior.selected_for_default) == is_selected
                        and prior.selection_order == position
                    )
                    if unchanged:
                        counters["skipped_existing"] += 1
                        continue
                    counters["readjudicated"] += 1
                session.add(EntrySourceEvidence(
                    lexicon_entry_id=created.id,
                    source_artifact_id=artifacts[item["source_id"]].id,
                    import_run_id=run_id,
                    normalized_word=entry["normalized_word"],
                    row_locator=item["line"],
                    field_kind=field,
                    sense_key=f"{field}@{item['line']}",
                    raw_word=raw_words.get(locator, entry["normalized_word"]),
                    raw_text=item["raw_value"],
                    evidence_sha256=item["idempotency_key"],
                    decision=decision,
                    selected_for_default=is_selected,
                    selection_order=position,
                    confirmed_by_username=administrator.username,
                    confirmed_at=moment,
                ))
                counters["written"] += 1
    session.flush()
    return counters
