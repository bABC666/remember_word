"""Resolve a user's explicit study choice or an existing NETEM recommendation."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Lexicon, User, UserSettings


def effective_lexicon_selection(session: Session, user: User) -> tuple[int | None, str]:
    settings = session.get(UserSettings, user.id)
    if settings is not None and settings.selected_lexicon_id is not None:
        lexicon = session.get(Lexicon, settings.selected_lexicon_id)
        if lexicon is not None and (
            lexicon.owner_user_id == user.id
            or (lexicon.owner_user_id is None and lexicon.visibility == "public")
        ):
            return lexicon.id, "explicit"
    recommended = session.scalar(
        select(Lexicon.id).where(
            Lexicon.owner_user_id.is_(None),
            Lexicon.visibility == "public",
            or_(func.lower(Lexicon.name).like("%netem%"), Lexicon.source_type == "netem"),
        ).order_by(Lexicon.id).limit(1)
    )
    if recommended is not None:
        return recommended, "recommended"
    return None, "none"
