"""Owner-scoped data access.

Every private business read goes through here so that ownership is decided in one
place, in the database query, from the authenticated user. Two rules:

* Ownership is never taken from a client supplied value. The caller passes the
  ``CurrentUser`` and nothing else decides whose data is returned.
* "Not yours" and "does not exist" are indistinguishable: both raise a 404. A 403
  would confirm that an identifier exists, which is exactly the information an
  IDOR probe is looking for.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Article,
    ImportBatch,
    Lexicon,
    LexiconEntry,
    User,
    UserLexicon,
    UserWordState,
)

#: One message for both "gone" and "not yours" so responses cannot be compared.
NOT_FOUND_WORD = "单词不存在"
NOT_FOUND_ARTICLE = "文章不存在"
NOT_FOUND_BATCH = "导入批次不存在"
NOT_FOUND_LEXICON = "词库不存在"
NOT_FOUND_SESSION = "会话不存在"


class NotFoundError(LookupError):
    """The resource does not exist, or does not belong to this user."""


def not_found(detail: str) -> NotFoundError:
    return NotFoundError(detail)


@dataclass(frozen=True)
class WordView:
    """Everything the API needs about one of the user's words."""

    state: UserWordState
    entry: LexiconEntry

    @property
    def legacy_word_id(self) -> int | None:
        return self.state.legacy_word_id

    @property
    def anchor(self) -> str:
        """User override when set, otherwise the reviewed lexicon anchor."""
        return self.state.anchor_override or self.entry.default_anchor

    @property
    def semantic_note(self) -> str:
        return self.state.semantic_note or self.entry.semantic_note


def load_user_word(
    session: Session, user: User, word_id: int, *, detail: str = NOT_FOUND_WORD
) -> WordView:
    """Resolve a word id **for this user only**, or raise ``NotFoundError``.

    ``word_id`` keeps the V1.1 meaning (the legacy ``word.id``) so existing
    clients keep working, but the lookup is scoped by ``user_id`` in SQL: another
    user's id simply does not match.
    """
    row = session.execute(
        select(UserWordState, LexiconEntry)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(UserWordState.user_id == user.id, UserWordState.legacy_word_id == word_id)
    ).first()
    if row is None:
        raise not_found(detail)
    state, entry = row
    return WordView(state=state, entry=entry)


def load_user_word_state(
    session: Session, user: User, state_id: int, *, detail: str = NOT_FOUND_WORD
) -> WordView:
    """Resolve one of **this user's words** by ``user_word_state.id``.

    A separate, explicitly named namespace: ``/api/words/state/{id}`` and
    ``/api/study/word-states/{id}/review`` use this and nothing else. The legacy
    routes address a word by its ``word.id`` and never fall back to this one --
    silently accepting either identifier made it impossible to tell which row a
    request actually reached.
    """
    row = session.execute(
        select(UserWordState, LexiconEntry)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(UserWordState.user_id == user.id, UserWordState.id == state_id)
    ).first()
    if row is None:
        raise not_found(detail)
    state, entry = row
    return WordView(state=state, entry=entry)


def get_or_create_word_state(
    session: Session, user: User, entry: LexiconEntry
) -> UserWordState:
    """Lazily create this user's learning state for a lexicon entry.

    State is created the first time a user actually studies a word. Joining a
    public lexicon must never pre-generate thousands of rows.

    The unique constraint ``(user_id, lexicon_entry_id)`` is the backstop: if two
    requests race, one insert fails and the existing row is returned instead of a
    duplicate.
    """
    existing = session.scalar(
        select(UserWordState).where(
            UserWordState.user_id == user.id,
            UserWordState.lexicon_entry_id == entry.id,
        )
    )
    if existing is not None:
        return existing

    state = UserWordState(user_id=user.id, lexicon_entry_id=entry.id)
    session.add(state)
    try:
        # The unique constraint (user_id, lexicon_entry_id) is the backstop; a
        # flush here surfaces a racing duplicate immediately instead of at commit.
        session.flush()
    except IntegrityError:
        session.rollback()
        again = session.scalar(
            select(UserWordState).where(
                UserWordState.user_id == user.id,
                UserWordState.lexicon_entry_id == entry.id,
            )
        )
        if again is None:
            raise
        return again
    except Exception:
        session.rollback()
        raise
    return state


def load_user_article(
    session: Session, user: User, article_id: int, *, with_children: bool = False
) -> Article:
    """Load an article only if this user owns it, else raise ``NotFoundError``."""
    query = select(Article).where(Article.id == article_id, Article.user_id == user.id)
    if with_children:
        query = query.options(
            selectinload(Article.exposures), selectinload(Article.word_lookups)
        )
    article = session.scalar(query)
    if article is None:
        raise not_found(NOT_FOUND_ARTICLE)
    return article


def load_user_batch(session: Session, user: User, batch_id: int) -> ImportBatch:
    """Load an import batch only if this user owns it, else raise ``NotFoundError``."""
    batch = session.scalar(
        select(ImportBatch)
        .where(
            ImportBatch.id == batch_id,
            ImportBatch.user_id == user.id,
            ImportBatch.is_deleted.is_(False),
        )
        .options(selectinload(ImportBatch.images), selectinload(ImportBatch.candidates))
    )
    if batch is None:
        raise not_found(NOT_FOUND_BATCH)
    return batch


def accessible_lexicon_ids(session: Session, user: User) -> list[int]:
    """Lexicons this user may read: enabled ones plus public system lexicons."""
    mine = select(UserLexicon.lexicon_id).where(UserLexicon.user_id == user.id)
    public = select(Lexicon.id).where(
        Lexicon.visibility == "public", Lexicon.owner_user_id.is_(None)
    )
    return sorted(set(session.scalars(mine).all()) | set(session.scalars(public).all()))


def load_readable_lexicon(session: Session, user: User, lexicon_id: int) -> Lexicon:
    """Load a lexicon the user may read: public, or their own private one."""
    lexicon = session.get(Lexicon, lexicon_id)
    if lexicon is None:
        raise not_found(NOT_FOUND_LEXICON)
    if lexicon.owner_user_id is None:
        if not lexicon.is_public:
            raise not_found(NOT_FOUND_LEXICON)
        return lexicon
    if lexicon.owner_user_id != user.id:
        raise not_found(NOT_FOUND_LEXICON)
    return lexicon


def load_writable_lexicon(session: Session, user: User, lexicon_id: int) -> Lexicon:
    """Load a lexicon the user may modify.

    A private lexicon is writable by its owner; a system lexicon only by an
    admin. Everything else is a 404, so a private lexicon's existence is not
    disclosed.
    """
    lexicon = session.get(Lexicon, lexicon_id)
    if lexicon is None:
        raise not_found(NOT_FOUND_LEXICON)
    if lexicon.owner_user_id is None:
        if not user.is_admin:
            raise not_found(NOT_FOUND_LEXICON)
        return lexicon
    if lexicon.owner_user_id != user.id:
        raise not_found(NOT_FOUND_LEXICON)
    return lexicon


def user_lexicon(session: Session, user: User, lexicon_id: int) -> UserLexicon | None:
    return session.scalar(
        select(UserLexicon).where(
            UserLexicon.user_id == user.id, UserLexicon.lexicon_id == lexicon_id
        )
    )
