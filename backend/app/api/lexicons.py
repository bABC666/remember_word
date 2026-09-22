from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import Lexicon, LexiconEntry, UserLexicon
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
    """Delete a private lexicon the caller owns.

    A system lexicon cannot be deleted through the API, not even by an admin, so
    the shared migration result cannot be destroyed by accident.
    """
    lexicon = load_readable_lexicon(session, user, lexicon_id)
    if lexicon.is_system:
        raise HTTPException(status_code=403, detail="系统公共词库不能通过接口删除")
    if lexicon.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="词库不存在")
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
