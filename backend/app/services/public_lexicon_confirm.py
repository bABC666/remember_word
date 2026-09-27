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
6. re-derives, from that re-read, the pinned **revision** of every row the plan cites
   and compares it against the revision the plan froze. A source that declares no
   revision is untouched by this; a source that declares one has to reproduce the same
   identifier for the same locator, or the plan is refused;
7. writes ``source_artifact``, ``public_import_run``, ``public_import_run_source``,
   ``entry_source_evidence`` and any new ``lexicon_entry`` rows **in a single
   transaction**; a failure rolls all of it back.

Three things it deliberately never does:

* it never updates an existing ``lexicon_entry``. A word already in the target
  lexicon is reported as a conflict and left exactly as it was, so an import cannot
  overwrite a definition a user is already studying;
* it never creates ``UserWordState`` or ``ReviewEvent``. Claiming new words is a
  separate path (design section 5); confirming writes content only;
* it issues **no ``UPDATE`` and no ``DELETE`` at all**, and never writes
  ``lexicon.entry_count``. Evidence is append-only: re-adjudicating the same source
  value in a later run appends a row carrying that run's identity instead of rewriting
  the earlier decision -- and a *changed revision* counts as a re-adjudication, not as
  "nothing new". The run row's own counters are decided before it is inserted, so not
  even the audit row is written twice.
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
from app.services.public_lexicon_preview import (
    PreviewMapping,
    RevisionDeclaration,
    preview_file,
)

STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED = "already_applied"

DECISION_SELECTED = "selected"
DECISION_NOT_SELECTED = "not_selected"

#: The one format this slice can re-read. Recorded per artifact so a later reader
#: can tell which reader produced a row's evidence.
ARTIFACT_FORMAT = "delimited-text-v1"

#: Returned by :func:`_revision_at` for a source that declares no revision at all.
#: Distinguishable from ``""``, which is a declared revision column holding an empty
#: cell -- the same distinction the plan keeps by omitting the key instead of writing
#: an empty one. Neither is ever filled in by deriving something from the line number.
_ABSENT = object()


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
        # The plan digest names the target lexicon but never its id, so a recorded run
        # can belong to a *different* lexicon that happens to share the name -- what a
        # rename plus a new lexicon under the old name leaves behind. Answering
        # ``already_applied`` there would tell the operator the import is done while
        # nothing was written to the target in front of them.
        if existing.target_lexicon_id != lexicon.id:
            raise ConfirmRefused(
                f"该计划摘要此前已确认到另一个公共词库（id="
                f"{existing.target_lexicon_id}），不是本次的目标「{lexicon.name}」"
                f"（id={lexicon.id}）。本次未写入任何内容；"
                "请重新生成计划并重新裁定。"
            )
        # Deliberately checked before the source files are re-read: a retry has to
        # report what the first confirmation did even if the files have since moved.
        return {
            "status": STATUS_ALREADY_APPLIED,
            "run_id": existing.run_id,
            "import_run_id": existing.id,
            "plan_sha256": existing.plan_sha256,
            "target_lexicon": {"id": lexicon.id, "name": lexicon.name},
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
    """Rebuild the mapping the plan froze -- including its revision declaration.

    The rebuild has to be complete. ``mapping_sha256`` is computed over the mapping the
    plan stored, so dropping any part of it changes the digest and refuses a plan that
    is in fact intact. That is the failure a missing declaration produced: every source
    declaring where its revisions come from became unconfirmable, which is safe but
    useless.

    The declaration is read back rather than re-derived from the manifest, and it is
    passed through validation instead of trusted: if the plan's own frozen block is
    malformed, ``RevisionDeclaration`` says so here, before anything is written.
    """
    frozen = source["mapping"]
    declared = frozen.get("revision")
    if declared is not None and not isinstance(declared, dict):
        raise ConfirmRefused(
            f"来源 {source['source_id']!r} 的修订声明不是对象，无法重新读取；请重新预览。"
        )
    try:
        revision = RevisionDeclaration(**declared) if declared is not None else None
    except (TypeError, ValueError) as error:
        # A frozen declaration that no longer validates is a plan this path cannot
        # re-read, so it refuses in the same voice as every other bad input rather than
        # escaping as a bare TypeError from the dataclass.
        raise ConfirmRefused(
            f"来源 {source['source_id']!r} 的修订声明无法重建：{error}；请重新预览。"
        ) from error
    return PreviewMapping(
        columns=dict(frozen["columns"]),
        required_fields=tuple(frozen["required_fields"]),
        encoding=frozen["encoding"],
        delimiter=frozen["delimiter"],
        revision=revision,
    )


def _reverify_evidence(
    previews: dict[str, dict[str, Any]], plan: dict[str, Any]
) -> None:
    """Compare the values the plan recorded against the values the files still hold.

    The file fingerprint already covers this, but re-deriving by locator is cheap and
    turns "the file is unchanged" into "the specific value this plan cites is still
    there", which is what the confirmation actually claims. The values the plan would
    *write* are re-derived as well -- see :func:`_reverify_written_values`, which is
    what makes "still there" apply to the content and not only to the citations. The
    pinned revision of each cited row is re-derived here too, by
    :func:`_reverify_revisions`.
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
                _reverify_revisions(
                    previews, item, word=entry["normalized_word"], field=field
                )
        _reverify_written_values(previews, entry)


def _reverify_revisions(
    previews: dict[str, dict[str, Any]],
    item: dict[str, Any],
    *,
    word: str,
    field: str,
) -> None:
    """Prove the plan's revision for one cited row is the one the file still states.

    A revision is what makes a locator reproducible: ``row_locator`` says which line,
    and the revision says which version of the page that line belonged to. So the same
    treatment the value gets applies to it -- re-derived from the source file and the
    plan's own locator, never taken on the plan's word. ``plan_sha256`` covers the
    frozen revision, and it is not a signature; this is the check that makes the stored
    revision a fact rather than a claim.

    A source that declares no revision is invisible here: the preview records no
    revision for its rows, and an evidence item without the key matches that absence.
    """
    derived = _revision_at(previews.get(str(item["source_id"])), item["line"])
    if derived is _ABSENT:
        # The source this plan re-read declares no revision at all, so there is nothing
        # to compare. An item that nevertheless carries one cannot be confirmed: the
        # plan would record a revision no re-read of this source can reproduce.
        if item.get("source_revision") is not None:
            raise ConfirmRefused(
                f"{word!r} 的 {field} 证据声明了修订号 "
                f"{item['source_revision']!r}，但来源 {item['source_id']!r} "
                "并未声明修订来源，无法重新推导；请重新预览并重新裁定。"
            )
        return
    if item.get("source_revision") != derived:
        raise ConfirmRefused(
            f"{word!r} 的 {field} 证据在 {item['source_id']} 第 {item['line']} 行的"
            f"修订号与计划不符（计划 {item.get('source_revision')!r}，"
            f"文件 {derived!r}），请重新预览并重新裁定。"
        )


def _revision_at(preview: dict[str, Any] | None, line: object) -> object:
    """The source file's own revision for one locator, or ``_ABSENT``."""
    if not preview or not isinstance(line, int):
        return _ABSENT
    for row in preview["rows"]:
        if row["line"] == line:
            return row.get("source_revision", _ABSENT)
    # The locator itself did not come back; ``_value_at`` reports that with ``None``,
    # and a revision has nothing to say about a row that is not there.
    return _ABSENT


def _reverify_written_values(
    previews: dict[str, dict[str, Any]], entry: dict[str, Any]
) -> None:
    """Prove the values this plan would write are the ones its own evidence names.

    ``plan_sha256`` detects an **accidental** edit; it is not a signature, and anyone
    who edits a plan can recompute it. That caller is already in the threat model --
    the provenance block is re-proved for exactly that reason -- and the loop above
    proves the evidence still matches the files. Neither proves that the values in
    ``default_snapshot``, which are what get written onto ``lexicon_entry``, are the
    ones that were selected: an edited snapshot would be stored while
    ``entry_source_evidence`` recorded the real source value as ``selected``, leaving
    an audit trail that contradicts the content it claims to justify.

    So every written value is re-derived from the locators the plan itself declares as
    selected. Word, phonetic and part of speech only have to be *one of* the declared
    values, because one decision may legitimately cite several rows while only the
    first is stored; the meanings have to be the declared list exactly, in order. A
    ``no_default`` decision genuinely selects nothing, so an empty declaration
    constrains nothing.
    """
    snapshot = entry.get("default_snapshot")
    word = entry["normalized_word"]
    if not isinstance(snapshot, dict):
        raise ConfirmRefused(
            f"{word!r} 的计划没有可写入的默认值快照，拒绝确认；请重新预览并重新裁定。"
        )
    locators = entry.get("default_evidence") or {}

    def declared_values(field: str) -> list[str]:
        """The plan's own frozen values at the locators it declares as selected."""
        frozen = {
            (item["source_id"], item["line"]): item["raw_value"]
            for item in entry["evidence"][field]
        }
        values: list[str] = []
        for locator in locators.get(field) or []:
            key = (locator["source_id"], locator["line"])
            if key not in frozen:
                raise ConfirmRefused(
                    f"{word!r} 的 {field} 选中了计划里没有证据的位置 "
                    f"{key[0]}:{key[1]}，拒绝确认；请重新预览并重新裁定。"
                )
            value = frozen[key].strip()
            if value not in values:
                values.append(value)
        return values

    written_meanings = list(snapshot.get("source_meanings") or [])
    declared_meanings = declared_values("meaning")
    if written_meanings != declared_meanings:
        raise ConfirmRefused(
            f"{word!r} 计划要写入的释义与它自己的证据不符："
            f"会写入 {written_meanings}，而声明选中的证据是 {declared_meanings}。"
            "计划摘要只能发现误改，谁改了计划都能重算摘要，"
            "因此写入值必须能由计划自己的证据重新推出；请重新预览并重新裁定。"
        )

    # The primary raw line is the anchor this slice promises can always be re-checked,
    # so it is compared against the source file itself and not against the plan.
    primary = locators.get("source_raw") or {}
    raw_line = _raw_at(previews.get(str(primary.get("source_id"))), primary.get("line"))
    if raw_line is None or snapshot.get("source_raw") != raw_line:
        raise ConfirmRefused(
            f"{word!r} 的 source_raw 与主来源行不符（计划 "
            f"{str(snapshot.get('source_raw'))[:40]!r}，文件 "
            f"{str(raw_line)[:40]!r}），拒绝确认；请重新预览并重新裁定。"
        )

    for field in ("word", "phonetic", "part_of_speech"):
        allowed = declared_values(field)
        written = str(snapshot.get(field) or "").strip()
        if allowed and written not in allowed:
            raise ConfirmRefused(
                f"{word!r} 计划要写入的 {field} 值 {written!r} 不在它声明选中的证据 "
                f"{allowed} 中，拒绝确认；请重新预览并重新裁定。"
            )


def _value_at(preview: dict[str, Any], line: int, field: str) -> str | None:
    for row in preview["rows"]:
        if row["line"] == line:
            return row["values"].get(field)
    return None


def _raw_at(preview: dict[str, Any] | None, line: object) -> str | None:
    """The source file's own line, verbatim, at one locator."""
    if not preview or not isinstance(line, int):
        return None
    for row in preview["rows"]:
        if row["line"] == line:
            return row["raw"]
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

    artifacts: dict[str, SourceArtifact] = {}
    artifacts_created: dict[str, bool] = {}
    sources_created = 0
    for source in plan["sources"]:
        artifact, created = _get_or_create_artifact(session, source, moment)
        artifacts[source["source_id"]] = artifact
        artifacts_created[source["source_id"]] = created
        sources_created += int(created)

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

    # The run's own counters are decided *before* the row exists, so nothing in this
    # module ever issues an UPDATE: the record of what an import did is inserted once,
    # complete, and cannot be observed or left half-written in any other state. The
    # evidence rows only need ``run.id``, so they are added right after the flush.
    evidence_rows, evidence = _plan_evidence(
        session,
        plan=plan,
        artifacts=artifacts,
        created_entries=created_entries,
        administrator=administrator,
        moment=moment,
    )
    run = PublicImportRun(
        plan_sha256=plan["plan_sha256"],
        run_id=plan["run_id"],
        target_lexicon_id=lexicon.id,
        confirmed_by_user_id=administrator.id,
        confirmed_by_username=administrator.username,
        confirmed_at=moment,
        status=STATUS_APPLIED,
        entries_created=len(created_entries),
        entries_matched=len(conflicts),
        evidence_written=evidence["written"],
        result_json={
            "sources_created": sources_created,
            "sources_reused": len(plan["sources"]) - sources_created,
            "conflicts": conflicts,
            "evidence": evidence,
        },
    )
    session.add(run)
    session.flush()

    for source in plan["sources"]:
        created = artifacts_created[source["source_id"]]
        session.add(PublicImportRunSource(
            import_run_id=run.id,
            source_artifact_id=artifacts[source["source_id"]].id,
            outcome="created" if created else "reused",
            detail=f"role={source['role']} file={source['file']['name']}",
        ))
    for row in evidence_rows:
        session.add(EntrySourceEvidence(import_run_id=run.id, **row))
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


def _latest_evidence_for(session: Session, key: str) -> dict[str, Any] | None:
    """The most recent recorded decision for one piece of source evidence.

    Read-only, and deliberately not scoped by run or by lexicon: the question is
    whether *this* source value was already adjudicated the same way, so a re-import
    of the same bytes under another target does not record a second identical decision.
    The revision the row recorded comes back with the decision, because "the same way"
    includes the revision the value was read at -- see :func:`_plan_evidence`.
    """
    prior = session.scalars(
        select(EntrySourceEvidence)
        .where(EntrySourceEvidence.evidence_sha256 == key)
        .order_by(EntrySourceEvidence.id.desc())
    ).first()
    if prior is None:
        return None
    return {
        "decision": prior.decision,
        "selected_for_default": bool(prior.selected_for_default),
        "selection_order": prior.selection_order,
        # Part of "unchanged": a stored revision that differs from the one this run
        # re-derived is a different record of the same source value, not a repeat.
        "source_revision": prior.source_revision,
    }


def _plan_evidence(
    session: Session,
    *,
    plan: dict[str, Any],
    artifacts: dict[str, SourceArtifact],
    created_entries: list[tuple[dict[str, Any], LexiconEntry]],
    administrator: User,
    moment: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Work out this run's adjudication of each source value, writing nothing.

    One row per field value the run actually considered. An identical row from an
    earlier run is not duplicated; a *different* decision on the same source position
    becomes a new row rather than an edit of the old one, so the earlier decision stays
    readable. Nothing here issues an UPDATE or a DELETE -- and nothing here writes at
    all: the rows are returned for the caller to insert, which is what lets the run row
    carry its final counters from the moment it is first inserted.

    "Identical" covers the pinned revision as well as the decision. The evidence key
    does not carry the revision -- it identifies the *value*, and a revision is metadata
    about where that value was read -- so a comparison that ignored it would report
    "nothing new" while the revision on record had in fact moved, leaving the stored
    evidence claiming a revision this run never read. A moved revision is a
    re-adjudication: it appends a row, exactly as a changed decision does.
    """
    counters = {"written": 0, "skipped_existing": 0, "readjudicated": 0}
    rows: list[dict[str, Any]] = []
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
                # What the plan froze, re-proved against the file by
                # ``_reverify_revisions`` before this runs. Sources that declare no
                # revision contribute the empty string, which is the column's own way
                # of saying "no link".
                revision = str(item.get("source_revision") or "")
                prior = _latest_evidence_for(session, item["idempotency_key"])
                if prior is not None:
                    unchanged = (
                        prior["decision"] == decision
                        and prior["selected_for_default"] == is_selected
                        and prior["selection_order"] == position
                        and prior["source_revision"] == revision
                    )
                    if unchanged:
                        counters["skipped_existing"] += 1
                        continue
                    counters["readjudicated"] += 1
                rows.append({
                    "lexicon_entry_id": created.id,
                    "source_artifact_id": artifacts[item["source_id"]].id,
                    "normalized_word": entry["normalized_word"],
                    "row_locator": item["line"],
                    "field_kind": field,
                    "sense_key": f"{field}@{item['line']}",
                    "raw_word": raw_words.get(locator, entry["normalized_word"]),
                    "raw_text": item["raw_value"],
                    "evidence_sha256": item["idempotency_key"],
                    "decision": decision,
                    "selected_for_default": is_selected,
                    "selection_order": position,
                    "source_revision": revision,
                    "confirmed_by_username": administrator.username,
                    "confirmed_at": moment,
                })
                counters["written"] += 1
    return rows, counters
