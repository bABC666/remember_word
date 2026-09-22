from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import Lexicon, LexiconEntry, UserLexicon, UserWordState
from app.schemas import LexiconCreateRequest, LexiconUpdateRequest
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
    """How many of this lexicon's entries already carry a user's learning state.

    ``user_word_state.lexicon_entry_id`` is declared ``ON DELETE CASCADE``, so this
    count is exactly how much learning progress a delete of the lexicon would
    destroy: the rows would not be detached, they would be gone.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(UserWordState)
            .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
            .where(LexiconEntry.lexicon_id == lexicon_id)
        )
        or 0
    )


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

    A lexicon whose entries already carry learning state is refused with 409.
    Deleting it would not stop at the lexicon: ``lexicon_entry`` cascades to
    ``user_word_state`` (``ON DELETE CASCADE``), so the caller's own review status,
    consecutive failures, next review time and counters would be deleted with it,
    and the exposure and review links that identify those words would be cleared
    to NULL. Losing study history to tidy up a library is never what the request
    meant, so the delete is refused and nothing is written.

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
                f"该词库中有 {learning_states} 个单词已经存在学习记录，"
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
