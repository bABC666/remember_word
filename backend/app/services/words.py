from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Word

LEARNING_FIELDS = {"anchor", "semantic_note", "status", "notes", "possible_issue"}


def apply_learning_update(session: Session, word: Word, values: dict[str, object]) -> Word:
    for key in LEARNING_FIELDS:
        if key in values:
            setattr(word, key, values[key])
    session.add(word)
    session.commit()
    session.refresh(word)
    return word
