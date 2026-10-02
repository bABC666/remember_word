from __future__ import annotations

import hashlib

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep, ensure_user_settings
from app.models import (
    Lexicon,
    LexiconEntry,
    PublicImportRun,
    PublicImportRunSource,
    ReviewEvent,
    SourceArtifact,
    UserLexicon,
    UserWordState,
)
from app.schemas import LexiconCreateRequest, LexiconUpdateRequest
from app.services.file_lexicons import parse_file
from app.services.lexicon_selection import effective_lexicon_selection
from app.services.public_lexicon_target_preflight import explicitly_unapproved
from app.services.userdata import (
    load_readable_lexicon,
    user_lexicon,
)

router = APIRouter(prefix="/api/lexicons", tags=["lexicons"])


def lexicon_dict(
    lexicon: Lexicon, *, enabled: bool | None = None, entry_count: int | None = None
) -> dict[str, object]:
    return {
        "id": lexicon.id,
        "name": lexicon.name,
        "description": lexicon.description,
        "visibility": lexicon.visibility,
        "source_type": lexicon.source_type,
        "is_system": lexicon.is_system,
        "owner_user_id": lexicon.owner_user_id,
        # A system lexicon is readable by every authenticated user ("public"
        # means logged-in users, never anonymous visitors).
        "can_write": lexicon.owner_user_id is not None,
        "entry_count": lexicon.entry_count if entry_count is None else entry_count,
        "enabled": enabled,
        "created_at": lexicon.created_at,
    }


def _count_entries(session, lexicon_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(LexiconEntry)
            .where(LexiconEntry.lexicon_id == lexicon_id)
        )
        or 0
    )


def _count_learning_states(session, lexicon_id: int) -> int:
    """Count actual progress, excluding untouched states created for a queue."""
    state_count = (
        session.scalar(
            select(func.count())
            .select_from(UserWordState)
            .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
            .where(
                LexiconEntry.lexicon_id == lexicon_id,
                or_(
                    UserWordState.status != "new",
                    UserWordState.last_review.is_not(None),
                    UserWordState.next_review_at.is_not(None),
                    UserWordState.recall_success > 0,
                    UserWordState.recall_fail > 0,
                    UserWordState.consecutive_failures > 0,
                    UserWordState.context_exposure > 0,
                    UserWordState.anchor_override != "",
                    UserWordState.semantic_note != "",
                    UserWordState.notes != "",
                    UserWordState.possible_issue.is_(True),
                ),
            )
        )
        or 0
    )
    review_count = session.scalar(
        select(func.count()).select_from(ReviewEvent)
        .join(LexiconEntry, LexiconEntry.id == ReviewEvent.lexicon_entry_id)
        .where(LexiconEntry.lexicon_id == lexicon_id)
    ) or 0
    return state_count + review_count


@router.get("")
def list_lexicons(user: CurrentUser, session: SessionDep) -> list[dict[str, object]]:
    """Public system lexicons plus the caller's own private lexicons.

    Never another user's private lexicon.
    """
    public = session.scalars(
        select(Lexicon).where(
            Lexicon.visibility == "public", Lexicon.owner_user_id.is_(None)
        )
    ).all()
    mine = session.scalars(
        select(Lexicon).where(Lexicon.owner_user_id == user.id)
    ).all()

    items: list[dict[str, object]] = []
    for lexicon in [*public, *mine]:
        membership = user_lexicon(session, user, lexicon.id)
        items.append(
            lexicon_dict(
                lexicon,
                enabled=membership.enabled if membership else None,
                entry_count=_count_entries(session, lexicon.id),
            )
        )
    return items


@router.get("/selection")
def read_selection(user: CurrentUser, session: SessionDep) -> dict[str, object]:
    lexicon_id, source = effective_lexicon_selection(session, user)
    return {"lexicon_id": lexicon_id, "source": source}


@router.post("/{lexicon_id}/select")
def select_lexicon(lexicon_id: int, user: CurrentUser, session: SessionDep) -> dict[str, object]:
    lexicon = load_readable_lexicon(session, user, lexicon_id)
    membership = user_lexicon(session, user, lexicon.id)
    if membership is None:
        membership = UserLexicon(user_id=user.id, lexicon_id=lexicon.id, enabled=True)
    else:
        membership.enabled = True
    settings = ensure_user_settings(session, user)
    settings.selected_lexicon_id = lexicon.id
    session.add_all((membership, settings))
    session.commit()
    return {"lexicon_id": lexicon.id, "source": "explicit"}


async def _parse_upload(file: UploadFile):
    content = await file.read(1024 * 1024 + 1)
    try:
        rows = parse_file(file.filename or "", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    digest = hashlib.sha256((file.filename or "").encode() + b"\0" + content).hexdigest()
    return rows, digest


@router.post("/file-preview")
async def preview_file_lexicon(user: CurrentUser, file: UploadFile = File(...)):
    """Read and classify a list; create no lexicon or word rows."""
    rows, digest = await _parse_upload(file)
    return {
        "sha256": digest,
        "rows": [row.as_dict() for row in rows],
        "counts": {status: sum(row.status == status for row in rows)
                   for status in ("valid", "duplicate", "error")},
    }


@router.post("/file-import", status_code=201)
async def import_file_lexicon(
    user: CurrentUser, session: SessionDep,
    file: UploadFile = File(...), name: str = Form(...),
    preview_sha256: str | None = Form(default=None),
):
    rows, digest = await _parse_upload(file)
    if not preview_sha256:
        raise HTTPException(status_code=400, detail="请先预览文件再导入")
    if preview_sha256 != digest:
        raise HTTPException(status_code=409, detail="文件已变化，请重新预览后导入")
    valid = [row for row in rows if row.status == "valid"]
    title = name.strip()
    if not title or len(title) > 200 or not valid:
        raise HTTPException(status_code=400, detail="词库名称不能为空，且文件须含有效单词")
    lexicon = Lexicon(owner_user_id=user.id, name=title,
                      description="用户上传的词库；释义由用户提供，未经平台核实",
                      visibility="private", source_type="user_file", entry_count=len(valid))
    session.add(lexicon)
    session.flush()
    session.add(UserLexicon(user_id=user.id, lexicon_id=lexicon.id, enabled=True))
    entries = []
    for sequence, row in enumerate(valid, 1):
        entry = LexiconEntry(
            lexicon_id=lexicon.id, word=row.word, normalized_word=row.word.casefold(),
            part_of_speech=row.part_of_speech,
            source_meanings=[row.meaning] if row.meaning else [],
            source_raw="", sequence=sequence,
        )
        entries.append(entry)
    session.add_all(entries)
    session.commit()
    return {**lexicon_dict(lexicon, enabled=True), "imported_count": len(valid)}


@router.post("", status_code=201)
def create_lexicon(
    payload: LexiconCreateRequest, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """Create a private lexicon owned by the caller.

    A user can only ever create their own lexicon: ownership comes from the
    session, and system (ownerless) lexicons are not creatable through the API at
    all.
    """
    lexicon = Lexicon(
        owner_user_id=user.id,
        name=payload.name,
        description=payload.description,
        visibility="private",
        source_type="manual",
    )
    session.add(lexicon)
    session.flush()
    session.add(UserLexicon(user_id=user.id, lexicon_id=lexicon.id, enabled=True))
    session.commit()
    session.refresh(lexicon)
    return lexicon_dict(lexicon, enabled=True, entry_count=0)


@router.get("/{lexicon_id}")
def get_lexicon(lexicon_id: int, user: CurrentUser, session: SessionDep) -> dict[str, object]:
    lexicon = load_readable_lexicon(session, user, lexicon_id)
    membership = user_lexicon(session, user, lexicon.id)
    return lexicon_dict(
        lexicon,
        enabled=membership.enabled if membership else None,
        entry_count=_count_entries(session, lexicon.id),
    )


@router.patch("/{lexicon_id}")
def update_lexicon(
    lexicon_id: int, payload: LexiconUpdateRequest, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """Rename or re-describe a lexicon the caller may write.

    Two different refusals, on purpose:

    * a **system** lexicon is visible to every authenticated user, so refusing a
      non-admin here is a missing *capability*: 403, which the spec requires;
    * another user's **private** lexicon answers 404, because its existence must
      not be disclosed.
    """
    lexicon = load_readable_lexicon(session, user, lexicon_id)
    if lexicon.is_system and not user.is_admin:
        raise HTTPException(status_code=403, detail="系统公共词库只有管理员可以修改")

    changes = payload.model_dump(exclude_unset=True)
    if lexicon.is_system:
        # A system lexicon is shared, so it stays public; an admin may only
        # correct its metadata.
        changes.pop("visibility", None)
    if "name" in changes and changes["name"] is not None:
        lexicon.name = changes["name"]
    if "description" in changes and changes["description"] is not None:
        lexicon.description = changes["description"]
    if "visibility" in changes and changes["visibility"] is not None:
        lexicon.visibility = changes["visibility"]
    session.add(lexicon)
    session.commit()
    session.refresh(lexicon)
    return lexicon_dict(lexicon, entry_count=_count_entries(session, lexicon.id))


@router.delete("/{lexicon_id}")
def delete_lexicon(lexicon_id: int, user: CurrentUser, session: SessionDep) -> dict[str, bool]:
    """Delete a private lexicon the caller owns, but never the progress inside it.

    A system lexicon cannot be deleted through the API, not even by an admin, so
    the shared migration result cannot be destroyed by accident.

    Untouched ``new`` states are only queue placeholders and may be deleted.
    Any review, changed state, note, exposure or schedule is real progress: deleting
    its entry would destroy state and detach review links, so return 409.

    Refusal order matters. Ownership and the system-lexicon rule are decided first,
    so another user's private lexicon -- and whether it holds learning records --
    is never confirmed to exist. Only an owner who may otherwise delete the
    lexicon can reach the 409.
    """
    lexicon = load_readable_lexicon(session, user, lexicon_id)
    if lexicon.is_system:
        raise HTTPException(status_code=403, detail="系统公共词库不能通过接口删除")
    if lexicon.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="词库不存在")

    learning_states = _count_learning_states(session, lexicon.id)
    if learning_states:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该词库中有 {learning_states} 条学习记录，"
                "删除词库会连同这些学习记录一起永久删除（复习进度、"
                "连续失败次数与下次复习时间都会丢失），因此已拒绝删除。"
                "词库与学习记录均已保留，未做任何修改。"
            ),
        )

    session.delete(lexicon)
    session.commit()
    return {"ok": True}


@router.post("/{lexicon_id}/enable")
def enable_lexicon(
    lexicon_id: int, user: CurrentUser, session: SessionDep, enabled: bool = True
) -> dict[str, object]:
    """Enrol the caller in a lexicon they may read.

    Only the caller's own ``user_lexicon`` row is touched. Learning state is not
    pre-generated: ``UserWordState`` rows appear lazily, on first study.
    """
    lexicon = load_readable_lexicon(session, user, lexicon_id)
    membership = user_lexicon(session, user, lexicon.id)
    if membership is None:
        membership = UserLexicon(user_id=user.id, lexicon_id=lexicon.id, enabled=enabled)
    else:
        membership.enabled = enabled
    session.add(membership)
    session.commit()
    return {"ok": True, "lexicon_id": lexicon.id, "enabled": enabled}


@router.get("/{lexicon_id}/sources")
def list_lexicon_sources(
    lexicon_id: int, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """The sources a public import recorded for **this** lexicon.

    Read through the import's own relations -- ``public_import_run`` for this
    ``target_lexicon_id``, then ``public_import_run_source``, then the artifact each
    row names. Never by scanning ``source_artifact`` and guessing an owner: an
    artifact carries no lexicon, two imports may legitimately share one (a reused
    file is the same artifact, by design), and the only thing that says which import
    used which file is the run's own link row.

    What this returns is the *declaration* an administrator recorded when the import
    was confirmed, not a verified right to publish. ``source_artifact`` stores what a
    submitter declared -- publisher, version, licence id, scope -- and the licence
    text itself is never read from disk here: the row carries only its fingerprint.
    ``storage_locator``, the mapping, the plan and every local path stay out of the
    response, because a reader needs to know *what was declared* and nothing about
    where this machine keeps the file.

    ``authorization_review`` states the limit in the payload itself. A licence field
    saying ``CC-BY-SA-4.0`` is a declaration, and a field saying "not approved" is
    too; neither is a machine-verified grant, and the report says so unconditionally.
    """
    lexicon = load_readable_lexicon(session, user, lexicon_id)

    rows = session.execute(
        select(PublicImportRunSource, SourceArtifact, PublicImportRun)
        .join(SourceArtifact, SourceArtifact.id == PublicImportRunSource.source_artifact_id)
        .join(PublicImportRun, PublicImportRun.id == PublicImportRunSource.import_run_id)
        .where(PublicImportRun.target_lexicon_id == lexicon.id)
        .order_by(PublicImportRun.confirmed_at, PublicImportRunSource.id)
    ).all()

    sources: dict[int, dict[str, object]] = {}
    for link, artifact, run in rows:
        item = sources.get(artifact.id)
        if item is None:
            item = {
                "source_artifact_id": artifact.id,
                "role": artifact.role,
                "name": artifact.name,
                "publisher": artifact.publisher,
                "version": artifact.version,
                "obtained_at_utc": artifact.obtained_at_utc,
                "format": artifact.format,
                "license_id": artifact.license_id,
                "license_text_sha256": artifact.license_text_sha256,
                "file_sha256": artifact.file_sha256,
                "mapping_sha256": artifact.mapping_sha256,
                "byte_size": artifact.byte_size,
                "use_scope": artifact.use_scope,
                "display_scope": artifact.display_scope,
                "runs": [],
            }
            # Decided after the declaration fields are in place, and kept apart from
            # the values above on purpose: these are what the *source* declares about
            # its own use, and the preflight's recogniser reads them to decide whether
            # a submitter marked the source explicitly unapproved.
            item["declared_approval_state"] = (
                "explicitly_unapproved"
                if explicitly_unapproved(_declared_text(item))
                else "not_assessed"
            )
            sources[artifact.id] = item
        item["runs"].append({
            "run_id": run.run_id,
            "confirmed_at": run.confirmed_at,
            "outcome": link.outcome,
        })

    pending = [
        source["name"] for source in sources.values()
        if source["declared_approval_state"] == "explicitly_unapproved"
    ]
    return {
        "lexicon": {"id": lexicon.id, "name": lexicon.name},
        "sources": list(sources.values()),
        "authorization_review": _authorization_review(pending),
    }


def _declared_text(item: dict[str, object]) -> dict[str, str]:
    """The declaration fields the unapproved-marker recogniser reads.

    Restated here rather than passed as the artifact row, so adding a column to this
    response cannot silently widen what the recogniser scans.
    """
    return {
        field: str(item.get(field) or "")
        for field in ("publisher", "version", "license_id", "use_scope", "display_scope")
    }


def _authorization_review(pending: list[str]) -> dict[str, object]:
    """Say, unconditionally, that a declared licence is not a verified grant."""
    if pending:
        return {
            "status": "pending_owner_approval",
            "pending_sources": pending,
            "message": (
                "来源声明明确写明未获批准；仍待负责人批准。"
                "本接口只回放已记录的声明，不核实许可有效性。"
            ),
        }
    return {
        "status": "not_assessed",
        "pending_sources": [],
        "message": (
            "以下许可与范围字段来自导入时记录的来源声明；本接口未核实许可真实性，"
            "也不代表授权已获确认。"
        ),
    }
