from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Word(Base):
    """V1.1 single-user word row.

    Superseded by LexiconEntry + UserWordState in V1.2, but kept untouched so the
    migration stays additive and reversible. The two compatibility columns below
    bridge the legacy row to its migrated entry and record the owner of the
    legacy learning state.
    """

    __tablename__ = "word"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Bridge to the migrated lexicon entry. Compatibility column.
    lexicon_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Owner of the legacy learning state. Compatibility column.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    word: Mapped[str] = mapped_column(String(160), index=True)
    phonetic: Mapped[str] = mapped_column(String(200), default="")
    part_of_speech: Mapped[str] = mapped_column(String(80), default="")
    source_meanings: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_raw: Mapped[str] = mapped_column(Text, default="")
    anchor: Mapped[str] = mapped_column(String(300), default="")
    semantic_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    last_review: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    recall_success: Mapped[int] = mapped_column(Integer, default=0)
    recall_fail: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    context_exposure: Mapped[int] = mapped_column(Integer, default=0)
    possible_issue: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    review_events: Mapped[list[ReviewEvent]] = relationship(
        back_populates="word", cascade="all, delete-orphan"
    )
    exposures: Mapped[list[ArticleWordExposure]] = relationship(
        back_populates="word", cascade="all, delete-orphan"
    )


class ImportBatch(Base):
    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Owner of the batch. Compatibility column added in V1.2.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="upload")
    provider: Mapped[str] = mapped_column(String(80), default="paddleocr")
    raw_ocr_text: Mapped[str] = mapped_column(Text, default="")
    raw_ocr_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_stage: Mapped[str] = mapped_column(String(32), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    images: Mapped[list[ImportImage]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )
    candidates: Mapped[list[ImportCandidate]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ImportImage(Base):
    __tablename__ = "import_image"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batch.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    batch: Mapped[ImportBatch] = relationship(back_populates="images")


class ImportCandidate(Base):
    __tablename__ = "import_candidate"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batch.id", ondelete="CASCADE"), index=True
    )
    word_id: Mapped[int | None] = mapped_column(ForeignKey("word.id"), nullable=True)
    #: Entry produced when this candidate was confirmed. Compatibility column.
    lexicon_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="SET NULL"), nullable=True, index=True
    )
    word: Mapped[str] = mapped_column(String(160), default="")
    phonetic: Mapped[str] = mapped_column(String(200), default="")
    part_of_speech: Mapped[str] = mapped_column(String(80), default="")
    source_meanings: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_raw: Mapped[str] = mapped_column(Text, default="")
    anchor: Mapped[str] = mapped_column(String(300), default="")
    semantic_note: Mapped[str] = mapped_column(Text, default="")
    possible_issue: Mapped[bool] = mapped_column(Boolean, default=False)
    issue_note: Mapped[str] = mapped_column(Text, default="")
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ai_raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    batch: Mapped[ImportBatch] = relationship(back_populates="candidates")


class Article(Base):
    __tablename__ = "article"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Owner of the article. Compatibility column added in V1.2.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    target_words: Mapped[list[str]] = mapped_column(JSON, default=list)
    actual_used_words: Mapped[list[str]] = mapped_column(JSON, default=list)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    translation: Mapped[str] = mapped_column(Text, default="")
    translated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    translation_ai_raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    exposures: Mapped[list[ArticleWordExposure]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    review_events: Mapped[list[ReviewEvent]] = relationship(back_populates="article")
    word_lookups: Mapped[list[ArticleWordLookup]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )

    def is_owned_by(self, user_id: int | None) -> bool:
        """Single source of truth for article ownership.

        Child rows (exposures, lookups, review events) inherit ownership through
        the article instead of duplicating ``user_id``, so a parent and child can
        never disagree about who owns the data.
        """
        return user_id is not None and self.user_id == user_id


class ArticleWordExposure(Base):
    """A word's real appearance in one of the user's articles.

    Ownership is derived from the article; this table deliberately carries no
    ``user_id`` so a parent and child can never disagree about the owner.
    """

    __tablename__ = "article_word_exposure"
    # Uniqueness is enforced by two partial indexes in migration 0006: one per
    # legacy word, one per lexicon entry, because a word added from reading by a
    # new user has no legacy word row.
    __table_args__ = (
        Index(
            "uq_article_word",
            "article_id",
            "word_id",
            unique=True,
            sqlite_where=text("word_id is not null"),
        ),
        Index(
            "uq_article_entry",
            "article_id",
            "lexicon_entry_id",
            unique=True,
            sqlite_where=text("lexicon_entry_id is not null"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("article.id", ondelete="CASCADE"), index=True
    )
    #: Legacy word row, when the word predates V1.2.
    word_id: Mapped[int | None] = mapped_column(
        ForeignKey("word.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: Lexicon entry, always known. Compatibility bridge from migration 0006.
    lexicon_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="SET NULL"), nullable=True, index=True
    )
    context: Mapped[str] = mapped_column(Text, default="")
    exposure_count: Mapped[int] = mapped_column(Integer, default=1)
    first_exposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_exposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    article: Mapped[Article] = relationship(back_populates="exposures")
    word: Mapped[Word | None] = relationship(back_populates="exposures")


class ArticleWordLookup(Base):
    __tablename__ = "article_word_lookup"
    __table_args__ = (
        UniqueConstraint("article_id", "normalized_word", name="uq_article_lookup_word"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("article.id", ondelete="CASCADE"), index=True
    )
    surface: Mapped[str] = mapped_column(String(160))
    normalized_word: Mapped[str] = mapped_column(String(160), index=True)
    phonetic: Mapped[str] = mapped_column(String(200), default="")
    part_of_speech: Mapped[str] = mapped_column(String(80), default="")
    meaning: Mapped[str] = mapped_column(String(500), default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(24), default="ai")
    added_word_id: Mapped[int | None] = mapped_column(
        ForeignKey("word.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ai_raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    article: Mapped[Article] = relationship(back_populates="word_lookups")


class ReviewEvent(Base):
    __tablename__ = "review_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Owner of the review. Authorization is derived from this column, never
    #: from a client supplied identifier.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Legacy word row, when the word predates V1.2.
    word_id: Mapped[int | None] = mapped_column(
        ForeignKey("word.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: Lexicon entry the review belongs to, always known.
    lexicon_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="SET NULL"), nullable=True, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    result: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(24))
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("article.id"), nullable=True, index=True
    )
    status_before: Mapped[str] = mapped_column(String(24))
    status_after: Mapped[str] = mapped_column(String(24))
    review_type: Mapped[str] = mapped_column(String(32), default="recall")

    word: Mapped[Word] = relationship(back_populates="review_events")
    article: Mapped[Article | None] = relationship(back_populates="review_events")


class HistoryEvent(Base):
    __tablename__ = "history_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: NULL marks an instance-level event such as an automatic backup.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    #: Argon2id hash, or the ``!`` sentinel while the password is unset.
    password_hash: Mapped[str] = mapped_column(String(255), default="!")
    role: Mapped[str] = mapped_column(String(24), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class UserSession(Base):
    """Server-side opaque session.

    Only the SHA-256 hash of the token is stored; the raw token exists solely in
    the caller's HttpOnly cookie, so a database leak does not hand out logins.
    """

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    user_agent: Mapped[str] = mapped_column(String(300), default="")

    user: Mapped[User] = relationship(back_populates="sessions")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    daily_new_words: Mapped[int] = mapped_column(Integer, default=15)
    article_length: Mapped[int] = mapped_column(Integer, default=650)
    onboarding_seen: Mapped[bool] = mapped_column(Boolean, default=False)
    theme: Mapped[str] = mapped_column(String(16), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship()


class Lexicon(Base):
    """A vocabulary list. ``owner_user_id IS NULL`` marks a system lexicon."""

    __tablename__ = "lexicon"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    source_type: Mapped[str] = mapped_column(String(32), default="manual")
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    entries: Mapped[list[LexiconEntry]] = relationship(
        back_populates="lexicon", cascade="all, delete-orphan"
    )

    @property
    def is_system(self) -> bool:
        return self.owner_user_id is None

    @property
    def is_public(self) -> bool:
        return self.visibility == "public"


class LexiconEntry(Base):
    """Lexicon content only. Never carries any user's learning state."""

    __tablename__ = "lexicon_entry"
    __table_args__ = (
        UniqueConstraint("lexicon_id", "normalized_word", name="uq_lexicon_entry_word"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lexicon_id: Mapped[int] = mapped_column(
        ForeignKey("lexicon.id", ondelete="CASCADE"), index=True
    )
    word: Mapped[str] = mapped_column(String(160))
    #: casefolded form of ``word``, stored so lookups never rely on runtime
    #: lower() comparisons and can be uniquely indexed.
    normalized_word: Mapped[str] = mapped_column(String(160), index=True)
    phonetic: Mapped[str] = mapped_column(String(200), default="")
    part_of_speech: Mapped[str] = mapped_column(String(80), default="")
    source_meanings: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_raw: Mapped[str] = mapped_column(Text, default="")
    #: Lexicon-side default anchor. A user's override lives in UserWordState.
    default_anchor: Mapped[str] = mapped_column(String(300), default="")
    semantic_note: Mapped[str] = mapped_column(Text, default="")
    possible_issue: Mapped[bool] = mapped_column(Boolean, default=False)
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: Populated only from imported external word-frequency data. Never from AI.
    frequency_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency_source: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    lexicon: Mapped[Lexicon] = relationship(back_populates="entries")


class UserLexicon(Base):
    """A user enabling a lexicon, with per-lexicon new-word pacing."""

    __tablename__ = "user_lexicon"
    __table_args__ = (UniqueConstraint("user_id", "lexicon_id", name="uq_user_lexicon"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    lexicon_id: Mapped[int] = mapped_column(
        ForeignKey("lexicon.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_new_words: Mapped[int] = mapped_column(Integer, default=15)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lexicon: Mapped[Lexicon] = relationship()


class UserWordState(Base):
    """One user's learning state for one lexicon entry."""

    __tablename__ = "user_word_state"
    __table_args__ = (
        UniqueConstraint("user_id", "lexicon_entry_id", name="uq_user_word_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    lexicon_entry_id: Mapped[int] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="CASCADE"), index=True
    )
    #: Migration bridge back to the V1.1 ``word`` row this state came from.
    legacy_word_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="new")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_review: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recall_success: Mapped[int] = mapped_column(Integer, default=0)
    recall_fail: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    context_exposure: Mapped[int] = mapped_column(Integer, default=0)
    #: Empty means "fall back to LexiconEntry.default_anchor".
    anchor_override: Mapped[str] = mapped_column(String(300), default="")
    semantic_note: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    possible_issue: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    entry: Mapped[LexiconEntry] = relationship()

    @property
    def anchor(self) -> str:
        return self.anchor_override or self.entry.default_anchor


# --- Phase 2.9: public lexicon import provenance -----------------------------
#
# These four tables record where shared public content came from and what a human
# decided about it. They are append-only by construction: nothing in the confirm
# path issues an UPDATE or a DELETE, so a later run that re-adjudicates the same
# source value adds its own row instead of rewriting the earlier decision.
#
# Two deliberate deviations from the first draft of
# ``docs/V1.2-PHASE2.9-CONFIRM-WRITE-DESIGN.md``, both in the direction the
# repository already takes elsewhere (see migration 0007's delete semantics):
#
# * ``entry_source_evidence.lexicon_entry_id`` is ``ON DELETE SET NULL`` rather
#   than ``CASCADE``. Evidence is an audit record of what a source said and what
#   was decided about it; deleting an entry must not erase that record, and the
#   row keeps its word identity through ``normalized_word``.
# * the evidence unique key is ``(evidence_sha256, import_run_id)`` rather than
#   ``evidence_sha256`` alone. Keying on the evidence alone would make a genuine
#   re-adjudication either impossible or a rewrite of history.


class SourceArtifact(Base):
    """One source file, read through one mapping, in one declared role.

    Identity is the file bytes plus the mapping, never the file name: the same
    bytes re-labelled in a manifest are still the same input, and a different
    mapping of the same file is a different one. A row is written once and never
    updated -- a new file version or a new mapping is a new artifact.
    """

    __tablename__ = "source_artifact"
    __table_args__ = (
        UniqueConstraint(
            "file_sha256", "mapping_sha256", "role", name="uq_source_artifact_identity"
        ),
        # The last line of defence for "no blank authorisation metadata". The plan
        # blocks on incomplete provenance and the confirmation refuses it in words;
        # this makes an artifact row with an empty publisher, version, acquisition
        # time, licence or scope impossible to insert at all, whatever path tries.
        CheckConstraint(
            "length(trim(publisher)) > 0"
            " AND length(trim(version)) > 0"
            " AND length(trim(obtained_at_utc)) > 0"
            " AND length(trim(license_id)) > 0"
            " AND length(trim(use_scope)) > 0"
            " AND length(trim(display_scope)) > 0",
            name="ck_source_artifact_provenance_present",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(120), default="")
    obtained_at_utc: Mapped[str] = mapped_column(String(40), default="")
    format: Mapped[str] = mapped_column(String(32), default="")
    #: The frozen mapping this artifact was read through, and its fingerprint.
    mapping_json: Mapped[str] = mapped_column(Text, default="")
    mapping_sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    #: Licence evidence. These record what a human declared, not what is true.
    license_id: Mapped[str] = mapped_column(String(80), default="")
    license_text_sha256: Mapped[str] = mapped_column(String(64), default="")
    use_scope: Mapped[str] = mapped_column(String(200), default="")
    display_scope: Mapped[str] = mapped_column(String(200), default="")
    storage_locator: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicImportRun(Base):
    """One administrator confirmation attempt, keyed by the plan it confirmed.

    ``plan_sha256`` is unique because it is the retry key: confirming the same plan
    twice must return the first run's result rather than write anything again.
    """

    __tablename__ = "public_import_run"
    __table_args__ = (
        UniqueConstraint("plan_sha256", name="uq_public_import_run_plan"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_sha256: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64))
    target_lexicon_id: Mapped[int] = mapped_column(
        ForeignKey("lexicon.id", ondelete="RESTRICT"), index=True
    )
    #: The confirming administrator. SET NULL on account deletion, with the name
    #: kept as an immutable snapshot, so removing an account cannot silently erase
    #: the record of who confirmed a public import.
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by_username: Mapped[str] = mapped_column(String(64), default="")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: applied | already_applied | refused
    status: Mapped[str] = mapped_column(String(16))
    entries_created: Mapped[int] = mapped_column(Integer, default=0)
    entries_matched: Mapped[int] = mapped_column(Integer, default=0)
    evidence_written: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_report_locator: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicImportRunSource(Base):
    """Which artifacts one run used, and what happened to each."""

    __tablename__ = "public_import_run_source"
    __table_args__ = (
        UniqueConstraint(
            "import_run_id", "source_artifact_id", name="uq_public_import_run_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_run_id: Mapped[int] = mapped_column(
        ForeignKey("public_import_run.id", ondelete="RESTRICT"), index=True
    )
    source_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifact.id", ondelete="RESTRICT"), index=True
    )
    #: created | reused  (whether this run was the first to record the artifact)
    outcome: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(Text, default="")


class EntrySourceEvidence(Base):
    """Append-only record of one source field value and the decision taken on it.

    Written inside the confirming transaction and never updated: ``raw_text`` is the
    source's own text verbatim, and ``decision``/``selected_for_default`` say what a
    human did with it. A later run that re-adjudicates the same source position
    appends a new row carrying its own ``import_run_id`` and ``confirmed_at``, so the
    earlier decision stays readable.
    """

    __tablename__ = "entry_source_evidence"
    __table_args__ = (
        UniqueConstraint(
            "evidence_sha256", "import_run_id", name="uq_entry_source_evidence"
        ),
        # The stored revision is compared byte-for-byte with what a re-read of the
        # source produces, and a link is built from it. Whitespace at either end would
        # make those two disagree while looking identical on screen. SQLite's
        # one-argument trim() removes spaces only; refusing control characters belongs
        # to the mapping declaration that supplies the value, not to this column.
        CheckConstraint(
            "source_revision = trim(source_revision)",
            name="ck_entry_source_evidence_revision_trimmed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The entry this evidence was adopted into, when this run wrote one. NULL after
    #: an entry is deleted, and for evidence recorded without an entry.
    lexicon_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifact.id", ondelete="RESTRICT"), index=True
    )
    import_run_id: Mapped[int] = mapped_column(
        ForeignKey("public_import_run.id", ondelete="RESTRICT"), index=True
    )
    #: Keeps the word identity when no entry was written.
    normalized_word: Mapped[str] = mapped_column(String(160), index=True)
    #: Physical line number in the source file.
    row_locator: Mapped[int] = mapped_column(Integer)
    field_kind: Mapped[str] = mapped_column(String(24))
    #: Identity of the sense within the row. One row carries one value per field in
    #: this format, so the locator is the identity; a future format that yields
    #: several senses per cell must extend this rather than reuse it.
    sense_key: Mapped[str] = mapped_column(String(80))
    raw_word: Mapped[str] = mapped_column(String(160), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    #: The plan's evidence idempotency key: file x mapping x line x field x value.
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    #: selected | not_selected
    decision: Mapped[str] = mapped_column(String(16))
    selected_for_default: Mapped[bool] = mapped_column(Boolean, default=False)
    selection_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_by_username: Mapped[str] = mapped_column(String(64), default="")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: The pinned revision of the source *page* this row's value was read from, when
    #: the source declares one (zh.wiktionary pins one ``oldid`` per word; a source
    #: pinned as a whole declares a single commit). Empty means "no revision known",
    #: which is the honest answer both for a source that declares none and for a row
    #: whose cell is empty -- so it is ``NOT NULL DEFAULT ''`` rather than nullable:
    #: there is exactly one way to say "unknown", and it is not also the way to say
    #: "this row forgot to record one".
    #:
    #: ``row_locator`` alone cannot stand in for it. A line number is only meaningful
    #: together with the revision of the file it indexes, and the point of this column
    #: is that a reader can get back to the exact page revision a displayed value came
    #: from without the source file still being on disk.
    #:
    #: Declared last because migration 0010 appends it: a table built by the migrations
    #: and one built by ``create_all`` then have the same column order.
    source_revision: Mapped[str] = mapped_column(String(64), default="")


# --- Phase 2.9 follow-up: the short meaning the study page shows -------------
#
# Four things in this schema can each be called "the meaning of this word", and the
# whole point of this section is that they stay apart:
#
# 1. ``entry_source_evidence.raw_text`` -- what a source actually said, byte for
#    byte, append-only (above);
# 2. ``lexicon_entry.source_raw`` -- the primary source's own line, never rewritten;
# 3. ``lexicon_entry.source_meanings`` -- the adjudicated *source default* snapshot;
# 4. ``entry_concise_meaning`` (here) -- a short simplified display value, which is
#    what the study page prefers.
#
# The fourth is not a fifth copy of the third. A source default may be long, full of
# traditional characters, or carry an entry's whole sense list; a short study value
# is deliberately allowed to cover fewer senses than the source lists, and may even
# be text no source contains at all -- provided it is labelled as such. Cramming it
# into ``default_anchor`` or over ``source_meanings`` would destroy the only record
# of what the source actually said, so it gets its own table instead.

CONCISE_MEANING_KINDS: tuple[str, ...] = ("source", "derived", "ai_supplement")
CONCISE_MEANING_STATUSES: tuple[str, ...] = ("candidate", "confirmed", "rejected")

#: The product rule is "one to three short, common senses" **per part of speech**, and
#: the cap is enforced by the database as well as the service so a later code path
#: cannot widen it silently; widening it is a product decision and therefore a
#: migration. The cap is per group: a word with two parts of speech may legitimately
#: show more than three values in total, which is why there is deliberately **no** cap
#: on the number of display rows a word may have.
CONCISE_MEANING_MAX_LENGTH = 40
CONCISE_MEANING_MAX_SLOTS = 3

#: The closed vocabulary of part-of-speech keys. A closed set is what makes grouping
#: deterministic: with free text, ``名詞``/``名词``/``n.``/``noun`` would become four
#: groups for one word and the unique slot index could not stop it. Adding a key is a
#: product decision and therefore a migration, the same rule ``MAX_SLOTS`` follows.
#:
#: The keys are English tags rather than the headings a source happens to use, because
#: the heading is *evidence for* a part of speech, not its identity: ``Wiktionary``
#: spells the same category ``形容詞`` on one page and ``形容词`` on another.
CONCISE_MEANING_POS_KEYS: tuple[str, ...] = (
    "noun",
    "verb",
    "adj",
    "adv",
    "pron",
    "det",
    "num",
    "prep",
    "conj",
    "interj",
    "particle",
    "classifier",
    "abbrev",
    "prefix",
    "suffix",
    "phrase",
)

#: Where a row's part of speech came from. ``none`` is the only value allowed while the
#: part of speech is undetermined, and it is the value a candidate starts life with:
#: a part of speech is never *inferred* from a display value, because that would be a
#: machine guess presented as a fact about the word.
CONCISE_MEANING_POS_SOURCES: tuple[str, ...] = ("none", "pos_section", "reviewer")

#: How "the part of speech is not established yet" is stored. Deliberately ``""`` and
#: not ``NULL``: migration 0010 established this repository's rule that a text-ish
#: column records "unknown" exactly one way, and that the way is ``NOT NULL DEFAULT ''``
#: rather than nullable. ``NULL`` would add a second spelling of the same state, and the
#: two would then have to be compared as equal in every constraint and query.
CONCISE_MEANING_POS_UNDETERMINED = ""

#: The languages a concise meaning may declare: the entry's own language, or "not
#: recorded". ``""`` again means "unknown" rather than "some other language".
#:
#: A part-of-speech grouping is not enough on its own. A zh.wiktionary page carries
#: several languages' sections -- the word ``mutter`` has Danish, Norwegian and Swedish
#: noun senses beside its English ones -- so a Chinese gloss taken from the wrong section
#: would otherwise be grouped as if it were an English sense. Recording the language is
#: what lets a reader check that, and the constraint below makes a row that declares
#: itself to be some *other* language unrepresentable in the first place.
CONCISE_MEANING_LANGUAGES: tuple[str, ...] = ("", "en")

#: ``pos_key``/``pos_source``/``language`` as SQL literal lists, built from the tuples so
#: a value can never be legal in Python and illegal in the database.
CONCISE_MEANING_POS_KEY_SQL = ", ".join(repr(key) for key in ("", *CONCISE_MEANING_POS_KEYS))
CONCISE_MEANING_POS_SOURCE_SQL = ", ".join(repr(source) for source in CONCISE_MEANING_POS_SOURCES)
CONCISE_MEANING_LANGUAGE_SQL = ", ".join(repr(language) for language in CONCISE_MEANING_LANGUAGES)

#: Server default for ``pos_order``, kept as a module-level object rather than written
#: inline as ``text("1")``.
#:
#: Both tables below declare a column named ``text``, and a name assigned anywhere in a
#: class body shadows the module import for every later line of that body -- so an inline
#: ``text("1")`` further down the class would call the *column* and raise
#: ``TypeError: 'MappedColumn' object is not callable``. Hoisting it also states the
#: intent: this is one object shared by the model and the migration's DDL.
CONCISE_MEANING_POS_ORDER_SERVER_DEFAULT = text("1")


class EntryConciseMeaning(Base):
    """One short display slot of one entry, with the provenance of its wording.

    ``provenance_kind`` says where the *wording* came from, and the two CHECK
    constraints make the weaker kinds carry what they must:

    * ``source`` -- the text is a value the source itself contains. It must name the
      source position it came from (``source_locator``).
    * ``derived`` -- the text is a documented modification of a source value
      (traditional to simplified, noise removed, re-worded, one sense extracted). It
      must name the source position **and** say what was changed, so a reader can
      check the claim against the source rather than trust it.
    * ``ai_supplement`` -- no source says this. It must say why it was added, and it
      is structurally forbidden from pointing at a source position, so a supplement
      cannot be dressed up as a quotation.

    ``status`` is what keeps an unconfirmed candidate off the page: the read path
    filters on ``confirmed``, and a ``confirmed`` row cannot exist without a named
    human and a timestamp.
    """

    __tablename__ = "entry_concise_meaning"
    __table_args__ = (
        # One row per display slot. A rejected row does not hold its slot: withdrawing a
        # value is how a displayed meaning is changed, so the freed slot has to be
        # usable again without deleting the record of what was withdrawn.
        #
        # ``pos_key`` is part of the slot because ``display_order`` is 1..3 *within a
        # part of speech*: without it a word's second part of speech could not have a
        # first slot. Two rows that are both still undetermined (``pos_key = ''``) do
        # collide here, which is the intended reading -- an unclassified candidate
        # occupies the undetermined group's slot until someone classifies it.
        Index(
            "uq_entry_concise_meaning_slot",
            "lexicon_entry_id",
            "pos_key",
            "display_order",
            unique=True,
            sqlite_where=text("status <> 'rejected'"),
        ),
        CheckConstraint(
            "length(trim(text)) > 0",
            name="ck_entry_concise_meaning_text_present",
        ),
        CheckConstraint(
            f"length(text) <= {CONCISE_MEANING_MAX_LENGTH}",
            name="ck_entry_concise_meaning_text_short",
        ),
        CheckConstraint(
            f"display_order between 1 and {CONCISE_MEANING_MAX_SLOTS}",
            name="ck_entry_concise_meaning_order_range",
        ),
        CheckConstraint(
            "provenance_kind in ('source', 'derived', 'ai_supplement')",
            name="ck_entry_concise_meaning_kind",
        ),
        CheckConstraint(
            "status in ('candidate', 'confirmed', 'rejected')",
            name="ck_entry_concise_meaning_status",
        ),
        # A supplement points at no source; everything else must point at one.
        CheckConstraint(
            "provenance_kind = 'ai_supplement' OR length(trim(source_locator)) > 0",
            name="ck_entry_concise_meaning_locator_for_source",
        ),
        # ...and a supplement may not carry a source pointer at all. This is the
        # difference between "an AI wrote this, a human approved it" and "an AI wrote
        # this and it is dressed up as a quotation": the second shape is not
        # representable, whatever a later code path intends.
        CheckConstraint(
            "provenance_kind <> 'ai_supplement'"
            " OR (length(trim(source_locator)) = 0 AND source_evidence_id IS NULL)",
            name="ck_entry_concise_meaning_supplement_has_no_source",
        ),
        CheckConstraint(
            "provenance_kind = 'source' OR length(trim(derivation_note)) > 0",
            name="ck_entry_concise_meaning_note_when_not_verbatim",
        ),
        # Nothing is displayed without a named human behind it.
        CheckConstraint(
            "status <> 'confirmed'"
            " OR (confirmed_at IS NOT NULL AND length(trim(confirmed_by_username)) > 0)",
            name="ck_entry_concise_meaning_confirmed_is_attributed",
        ),
        # --- the part-of-speech group (migration 0011) -------------------------
        # A key outside the closed vocabulary would silently create a new group, which
        # is exactly the drift the closed set exists to prevent.
        CheckConstraint(
            f"pos_key in ({CONCISE_MEANING_POS_KEY_SQL})",
            name="ck_entry_concise_meaning_pos_key",
        ),
        CheckConstraint(
            f"pos_source in ({CONCISE_MEANING_POS_SOURCE_SQL})",
            name="ck_entry_concise_meaning_pos_source",
        ),
        # A key is stored already trimmed, so two rows cannot differ by invisible
        # whitespace while claiming to be the same group.
        CheckConstraint(
            "pos_key = trim(pos_key)",
            name="ck_entry_concise_meaning_pos_key_trimmed",
        ),
        # "Not established" and "no source for it" are the same state, so they are
        # forced to agree in both directions. This is what makes an undetermined part of
        # speech impossible to *store* as though a source had labelled it.
        CheckConstraint(
            "(pos_key = '' AND pos_source = 'none')"
            " OR (pos_key <> '' AND pos_source <> 'none')",
            name="ck_entry_concise_meaning_pos_key_matches_source",
        ),
        # Either the part of speech is undetermined and carries no evidence, or it is
        # established and says *where from* -- a heading in the pinned revision, or a
        # reviewer's own judgement. There is no third shape, so a part of speech cannot
        # be recorded as a bare assertion.
        CheckConstraint(
            "(pos_source = 'none' AND length(trim(pos_evidence_locator)) = 0)"
            " OR (pos_source <> 'none' AND length(trim(pos_evidence_locator)) > 0)",
            name="ck_entry_concise_meaning_pos_evidence",
        ),
        # Group positions are 1-based. There is deliberately no upper bound: the number
        # of parts of speech a word has is a fact about the word, not a product quota,
        # and a quota here would silently drop a real group. The per-group cap that *is*
        # a product rule is ``display_order between 1 and 3`` above.
        CheckConstraint(
            "pos_order >= 1",
            name="ck_entry_concise_meaning_pos_order_positive",
        ),
        # A row may not declare itself to be in some other language. See
        # ``CONCISE_MEANING_LANGUAGES``: the empty string means "not recorded", and a
        # source page's Danish or French section must not be storable as an English
        # sense. Recording "not recorded" is still possible, so this closes the
        # *declared* leak rather than pretending it closes every one.
        CheckConstraint(
            f"language in ({CONCISE_MEANING_LANGUAGE_SQL})",
            name="ck_entry_concise_meaning_language",
        ),
        CheckConstraint(
            "language = trim(language)",
            name="ck_entry_concise_meaning_language_trimmed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lexicon_entry_id: Mapped[int] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="CASCADE"), index=True
    )
    #: 1-based position in the study list. One entry may show at most three.
    display_order: Mapped[int] = mapped_column(Integer)
    #: The short, simplified value actually shown.
    text: Mapped[str] = mapped_column(String(200))
    #: source | derived | ai_supplement
    provenance_kind: Mapped[str] = mapped_column(String(16))
    #: The evidence row this wording came from, when there is one. SET NULL keeps the
    #: display value readable if the evidence row is ever removed.
    source_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("entry_source_evidence.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Human-readable source position, e.g. ``primary:12``. Survives evidence deletion,
    #: which is why it is a column of its own rather than a join.
    source_locator: Mapped[str] = mapped_column(String(200), default="")
    #: What was changed, or why a supplement was added.
    derivation_note: Mapped[str] = mapped_column(Text, default="")
    #: candidate | confirmed | rejected
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    proposed_by_username: Mapped[str] = mapped_column(String(64), default="")
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by_username: Mapped[str] = mapped_column(String(64), default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    #: --- the part-of-speech group (migration 0011) ---------------------------
    #:
    #: Declared last because migration 0011 appends them: a table built by the
    #: migrations and one built by ``create_all`` then have the same column order.
    #:
    #: ``pos_key`` is the group identity, ``pos_label`` is only how it is shown, and
    #: ``pos_order`` is which group comes first. Keeping the identity separate from the
    #: label is what makes renaming a label a display change instead of a regrouping.
    pos_key: Mapped[str] = mapped_column(
        String(24), default=CONCISE_MEANING_POS_UNDETERMINED, server_default=""
    )
    #: Shown for the group, e.g. ``动词``. Never used for grouping.
    pos_label: Mapped[str] = mapped_column(String(40), default="", server_default="")
    #: 1-based order of the group within the entry.
    #:
    #: ``server_default`` is declared as well as the Python default so a table built by
    #: ``create_all`` and one built by the migrations agree: without it, a writer that
    #: does not name this column (every pre-0011 writer) would get a NOT NULL violation
    #: on a ``create_all`` database and silently succeed on a migrated one.
    pos_order: Mapped[int] = mapped_column(
        Integer, default=1, server_default=CONCISE_MEANING_POS_ORDER_SERVER_DEFAULT
    )
    #: none | pos_section | reviewer
    pos_source: Mapped[str] = mapped_column(
        String(16), default="none", server_default="none"
    )
    #: Where the part of speech came from: the heading in the pinned revision
    #: (``zhwiktionary:9576029:12``) for ``pos_section``, or the gloss line itself for a
    #: reviewer's own call. Empty exactly when ``pos_source = 'none'``.
    pos_evidence_locator: Mapped[str] = mapped_column(
        String(200), default="", server_default=""
    )
    #: The language this sense belongs to; ``''`` means "not recorded".
    language: Mapped[str] = mapped_column(String(16), default="", server_default="")

    entry: Mapped[LexiconEntry] = relationship()
    #: Additional source positions this one display value rests on, beyond the primary
    #: ``source_locator`` above. Deleting the value deletes them: they describe it.
    citations: Mapped[list[EntryConciseMeaningCitation]] = relationship(
        back_populates="meaning",
        cascade="all, delete-orphan",
        order_by="EntryConciseMeaningCitation.citation_order",
    )

    @property
    def is_confirmed(self) -> bool:
        """Only the status half of "may this be shown".

        Deliberately **not** called ``is_displayable``: displayability also requires an
        established part of speech, a stated basis with a position, the target language
        and -- for a supplement -- no citations. That rule lives in one place,
        ``app.services.concise_meaning.display_refusal_reason``, so the model cannot
        hold a second, weaker copy of it that a reader might mistake for the real one.
        """
        return self.status == "confirmed"


class EntryConciseMeaningRevision(Base):
    """Append-only record of every proposal, confirmation and withdrawal.

    ``entry_concise_meaning`` answers "what does the study page show now"; this table
    answers "who put it there, when, and what did it say before". The service never
    issues an UPDATE or a DELETE against it, and a test enforces that with a
    statement hook, so the history cannot be edited by a later code path that only
    meant to change the current value.

    ``lexicon_entry_id`` is ``ON DELETE SET NULL`` for the same reason migration
    0008's evidence rows are: the record of a human's decision about a word should
    outlive the entry, and ``normalized_word`` keeps the word identity when it does.
    """

    __tablename__ = "entry_concise_meaning_revision"

    id: Mapped[int] = mapped_column(primary_key=True)
    lexicon_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("lexicon_entry.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Keeps the word identity when the entry is gone.
    normalized_word: Mapped[str] = mapped_column(String(160), index=True)
    concise_meaning_id: Mapped[int | None] = mapped_column(
        ForeignKey("entry_concise_meaning.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: proposed | confirmed | rejected
    action: Mapped[str] = mapped_column(String(16), index=True)
    display_order: Mapped[int] = mapped_column(Integer)
    #: The value as it stood when this action was taken.
    text: Mapped[str] = mapped_column(String(200), default="")
    provenance_kind: Mapped[str] = mapped_column(String(16), default="")
    source_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("entry_source_evidence.id", ondelete="SET NULL"), nullable=True
    )
    source_locator: Mapped[str] = mapped_column(String(200), default="")
    derivation_note: Mapped[str] = mapped_column(Text, default="")
    #: The account that performed the action. SET NULL plus the username snapshot, so
    #: deleting an account cannot erase who decided what the shared lexicon shows.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    #: --- the group that was in force (migration 0011) ------------------------
    #:
    #: The history has to record the grouping as well as the wording. Without these,
    #: "who moved this value from the noun group to the verb group, and when" is not
    #: answerable from the append-only record -- and moving a value between groups is a
    #: change to what the study page shows, so it belongs here rather than only in the
    #: current row.
    #:
    #: They are snapshots, so they carry no constraints of their own: a revision that
    #: says ``''``/``none`` is the honest record of a proposal made before anyone had
    #: established the part of speech.
    pos_key: Mapped[str] = mapped_column(String(24), default="", server_default="")
    pos_order: Mapped[int] = mapped_column(
        Integer, default=1, server_default=CONCISE_MEANING_POS_ORDER_SERVER_DEFAULT
    )
    pos_source: Mapped[str] = mapped_column(String(16), default="none", server_default="none")


class EntryConciseMeaningCitation(Base):
    """One additional source position a displayed value rests on.

    A display value may rest on more than one position, and on more than one *source*:
    the trial record's ``decrease`` shows ``减少；降低`` merging a zh.wiktionary line with
    a WikDict value, and ``performance`` shows two display values that both come from a
    single zh.wiktionary line. Neither shape fits the single ``source_locator`` /
    ``source_evidence_id`` pair on the meaning row, and neither fits a delimited string:
    only a row can carry its own ``source_evidence_id``, and pointing at the evidence is
    what makes the value checkable without the source file still being on disk.

    The primary citation stays on ``entry_concise_meaning`` rather than moving here.
    That keeps every CHECK that already guards it in force -- a non-supplement must name
    a position, a supplement must name none -- and leaves this table meaning strictly
    "the additional ones". The cost is that reading a value's full provenance means
    reading both the row and its citations, which is why ``entry_provenance`` is the one
    place that does it.

    ``ON DELETE CASCADE`` on the meaning: a citation describes a display value and has
    no meaning of its own once the value is gone. ``ON DELETE SET NULL`` on the evidence
    row, matching the meaning row's own foreign key, so losing an evidence row degrades
    the link but never deletes the citation or the value it supports.
    """

    __tablename__ = "entry_concise_meaning_citation"
    __table_args__ = (
        # 1-based, and unique per value: a citation list is a list, not a bag, and a
        # duplicated position would render twice.
        Index(
            "uq_entry_concise_meaning_citation_order",
            "concise_meaning_id",
            "citation_order",
            unique=True,
        ),
        CheckConstraint(
            "citation_order >= 1",
            name="ck_entry_concise_meaning_citation_order_positive",
        ),
        # A citation whose position is blank cannot be checked against anything, which is
        # the one thing a citation is for.
        CheckConstraint(
            "length(trim(citation_locator)) > 0",
            name="ck_entry_concise_meaning_citation_locator_present",
        ),
        CheckConstraint(
            "citation_locator = trim(citation_locator)",
            name="ck_entry_concise_meaning_citation_locator_trimmed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    concise_meaning_id: Mapped[int] = mapped_column(
        ForeignKey("entry_concise_meaning.id", ondelete="CASCADE"), index=True
    )
    #: 1-based position in this value's citation list.
    citation_order: Mapped[int] = mapped_column(Integer)
    #: Human-readable source position, e.g. ``zhwiktionary:9576029:15`` or ``wikdict:37``.
    #: A column of its own rather than a join, for the same reason the primary locator
    #: is: it has to survive the evidence row being removed.
    citation_locator: Mapped[str] = mapped_column(String(200))
    #: The evidence row this position resolves to, when the import recorded one.
    source_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("entry_source_evidence.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    meaning: Mapped[EntryConciseMeaning] = relationship(back_populates="citations")


# --- Phase 2.9 follow-up: the pinned wikitext line behind a citation ---------
#
# Two different things are called "the line a value came from", and migration 0012 is
# what keeps them apart:
#
# 1. ``entry_source_evidence.row_locator`` -- the physical line of a *converted* file.
#    For ``prior`` that is line 139 of ``zhwiktionary-v4en.csv``, and its ``zh_meaning``
#    cell aggregates several senses into one field.
# 2. ``source_wikitext_line`` (below) -- a line of the *wikitext* of one pinned page
#    revision. For the same word those are ``9576029:12`` (the ``===形容詞===`` heading),
#    ``:15``/``:16`` (glosses under it) and ``:23`` (a gloss under ``===副詞===``).
#
# ``139`` and ``15`` are both integers and both mean "the line"; they index different
# files and neither can stand in for the other. The CSV row was already storable. This
# table is the missing half: one row per cited line of one page revision, with the
# page's own fingerprint, the headings that govern the line, and -- when the line is
# itself a part-of-speech heading -- the heading's text and the key it maps to.
#
# What it deliberately does **not** do: it binds no citation and no part of speech. The
# bindings and the confirmation rules that read them are a later slice; this table only
# makes the line a thing that can be pointed at.

#: How the two path columns are joined, spelled once so a stored path and the string a
#: reader compares it with cannot drift apart. It is the same ``' > '`` the extraction
#: index writes in its ``section`` field.
SOURCE_WIKITEXT_LINE_PATH_SEPARATOR = " > "

#: ``pos_heading_key`` as a SQL literal list, built from the same tuple migration 0011
#: uses for the display grouping: one closed vocabulary of parts of speech, so a heading
#: can never establish a part of speech no group can hold.
SOURCE_WIKITEXT_LINE_POS_KEY_SQL = ", ".join(
    repr(key) for key in ("", *CONCISE_MEANING_POS_KEYS)
)


def source_wikitext_line_sha256(
    *,
    source_id: str,
    page_revision: str,
    page_text_sha256: str,
    line_number: int,
    raw_text: str,
) -> str:
    """The fingerprint of one recorded line, as stored in ``line_sha256``.

    ``sha256`` over the five fields joined by a newline, in the order above, encoded as
    UTF-8 and written as lowercase hex. The serialization is unambiguous because no
    field can contain a newline: ``raw_text`` and ``source_id`` are refused if they do
    (``ck_source_wikitext_line_text_single_line`` and
    ``ck_source_wikitext_line_source_id_single_line``), and ``page_revision`` is a run of
    digits.

    The page's own fingerprint is part of the formula on purpose. A hash over the line
    text alone would be identical for the same words appearing in two different pages or
    in two revisions of one page, and would then prove nothing about *this* citation;
    binding the page digest means a row cannot be moved to another page or another
    revision and still verify.

    Computed here rather than left to each writer, because the value is only useful if
    the confirmation step recomputes exactly the same digest: two spellings of the
    formula would make every stored row look tampered with.
    """
    joined = "\n".join(
        (source_id, page_revision, page_text_sha256, str(line_number), raw_text)
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class SourceWikitextLine(Base):
    """One line of one pinned revision of one source page, stored verbatim.

    Written once and never updated. The row is the answer to "what did line 15 of
    oldid 9576029 actually say", and it stays that answer after the preserved archive
    is moved, re-packed or lost -- which is the whole reason the line is copied into
    the database instead of being read from a file at display time.

    Identity is ``(source_id, page_revision, line_number)``: one revision of one page
    has exactly one line 15, so a second row for the same triple is refused rather than
    overwriting or joining the first. ``source_id`` is part of the key because an
    ``oldid`` is unique only inside the wiki that issued it.

    ``source_artifact_id`` names the preserved bytes this line was read from, and its
    ``file_sha256``/``mapping_sha256`` are the artifact fingerprint; ``page_text_sha256``
    is the digest of the whole page text and ``line_sha256`` the digest of this row, so
    both the page and the line can be re-checked against a re-read of the archive.

    Three of the rules that make it evidence are **triggers**, and they exist only in the
    database the migrations build: SQLAlchemy has no construct for them, so a table made
    by ``create_all`` would enforce the columns and the CHECKs and none of this. The rows
    are append-only -- no UPDATE and no DELETE, because a recorded line is what the page
    said and a silent rewrite of it would be a source reading that nobody took -- and one
    ``(source_id, page_revision)`` may carry only one ``page_text_sha256``, because an
    ``oldid`` names one immutable revision and a second digest for it is one of the two
    being wrong. ``backend/tests/test_source_wikitext_line.py`` asserts both the presence
    of the triggers and their effect, and the migration asserts them on the way in.
    """

    __tablename__ = "source_wikitext_line"
    __table_args__ = (
        # One row per position. Deliberately *not* keyed on the artifact: an oldid names
        # one immutable revision upstream, so two rows for the same triple would be two
        # spellings of one fact, and a reader looking the locator up could not tell which
        # of them a citation meant.
        Index(
            "uq_source_wikitext_line_position",
            "source_id",
            "page_revision",
            "line_number",
            unique=True,
        ),
        # --- the declared source ------------------------------------------------
        CheckConstraint(
            "length(source_id) > 0",
            name="ck_source_wikitext_line_source_id_present",
        ),
        CheckConstraint(
            "source_id = trim(source_id)",
            name="ck_source_wikitext_line_source_id_trimmed",
        ),
        # A locator is ``source_id:revision:line``, so an id containing ``:`` would make
        # the string ambiguous -- and the decision record's rule is that a source is
        # resolved through a declared alias, never by reading a prefix off the string.
        CheckConstraint(
            "instr(source_id, ':') = 0",
            name="ck_source_wikitext_line_source_id_unambiguous",
        ),
        # Unambiguous *and* one line: the id is one of the fields joined into
        # ``line_sha256``, and that serialization is only unambiguous while no field can
        # contain a line break.
        CheckConstraint(
            "instr(source_id, char(10)) = 0 AND instr(source_id, char(13)) = 0",
            name="ck_source_wikitext_line_source_id_single_line",
        ),
        # --- the page revision and the line -------------------------------------
        CheckConstraint(
            "length(page_revision) > 0",
            name="ck_source_wikitext_line_revision_present",
        ),
        CheckConstraint(
            "page_revision = trim(page_revision)",
            name="ck_source_wikitext_line_revision_trimmed",
        ),
        # An oldid is a run of digits, which is also what stops a CSV row number, a
        # commit hash or a free-form version string from being stored as a page
        # revision: a line here is a line of a *page*, or it is not storable at all.
        CheckConstraint(
            "page_revision NOT GLOB '*[^0-9]*'",
            name="ck_source_wikitext_line_revision_digits",
        ),
        CheckConstraint(
            "line_number >= 1",
            name="ck_source_wikitext_line_number_positive",
        ),
        # ...and a *number*. SQLite keeps text that does not look numeric in an INTEGER
        # column and sorts every INTEGER before every TEXT, so a stored ``'abc'`` would
        # compare as "after line 15" while every reader treats the column as a position.
        CheckConstraint(
            "typeof(line_number) = 'integer'",
            name="ck_source_wikitext_line_number_integer",
        ),
        # --- the line's own text ------------------------------------------------
        # A cited line with nothing in it proves nothing. Not trimmed: wikitext
        # indentation and list markers are content, not noise.
        CheckConstraint(
            "length(trim(raw_text)) > 0",
            name="ck_source_wikitext_line_text_present",
        ),
        # One line, literally. A CR or LF would make the stored text a block, and the
        # line number would then no longer say which text a citation means.
        CheckConstraint(
            "instr(raw_text, char(10)) = 0 AND instr(raw_text, char(13)) = 0",
            name="ck_source_wikitext_line_text_single_line",
        ),
        # --- the paths ----------------------------------------------------------
        CheckConstraint(
            "language_path = trim(language_path) AND heading_path = trim(heading_path)",
            name="ck_source_wikitext_line_paths_trimmed",
        ),
        CheckConstraint(
            "instr(language_path, char(10)) = 0 AND instr(language_path, char(13)) = 0"
            " AND instr(heading_path, char(10)) = 0 AND instr(heading_path, char(13)) = 0",
            name="ck_source_wikitext_line_paths_single_line",
        ),
        # When a language is recorded, the heading path is that language itself or a
        # sub-path under it. ``substr`` rather than ``LIKE``: a path may contain ``%``
        # or ``_``, and a pattern match would treat those as wildcards and accept a
        # heading path that is not under the recorded language at all.
        CheckConstraint(
            "length(language_path) = 0"
            " OR heading_path = language_path"
            " OR substr(heading_path, 1, length(language_path) + 3)"
            f" = language_path || '{SOURCE_WIKITEXT_LINE_PATH_SEPARATOR}'",
            name="ck_source_wikitext_line_heading_path_under_language",
        ),
        # --- the part-of-speech heading basis -----------------------------------
        CheckConstraint(
            f"pos_heading_key in ({SOURCE_WIKITEXT_LINE_POS_KEY_SQL})",
            name="ck_source_wikitext_line_pos_heading_key",
        ),
        CheckConstraint(
            "pos_heading_key = trim(pos_heading_key)",
            name="ck_source_wikitext_line_pos_heading_key_trimmed",
        ),
        CheckConstraint(
            "pos_heading_text = trim(pos_heading_text)",
            name="ck_source_wikitext_line_pos_heading_text_trimmed",
        ),
        # The key and the heading it was read from stand or fall together, in both
        # directions: no key without its heading text, and no heading text that claims
        # no part of speech. A part of speech is therefore never a bare assertion here.
        CheckConstraint(
            "(pos_heading_key = '' AND length(pos_heading_text) = 0)"
            " OR (pos_heading_key <> '' AND length(pos_heading_text) > 0)",
            name="ck_source_wikitext_line_pos_heading_agrees",
        ),
        CheckConstraint(
            "instr(pos_heading_text, char(10)) = 0"
            " AND instr(pos_heading_text, char(13)) = 0",
            name="ck_source_wikitext_line_pos_heading_text_single_line",
        ),
        # --- the fingerprints ---------------------------------------------------
        # Lowercase hex, exactly 64 characters: a truncated digest, or one in another
        # alphabet, cannot be compared byte-for-byte with a digest recomputed from the
        # preserved page, and a fingerprint that cannot be compared is not one.
        CheckConstraint(
            "length(page_text_sha256) = 64"
            " AND page_text_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_source_wikitext_line_page_text_fingerprint",
        ),
        CheckConstraint(
            "length(line_sha256) = 64 AND line_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_source_wikitext_line_line_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: The preserved file the line was read from. ``RESTRICT``, matching
    #: ``entry_source_evidence``: the bytes a line was read from cannot be removed out
    #: from under the record of that read.
    source_artifact_id: Mapped[int] = mapped_column(
        ForeignKey("source_artifact.id", ondelete="RESTRICT"), index=True
    )
    #: The **declared** source id, e.g. ``zhwiktionary-pinned-oldid`` -- not the short
    #: name a locator string uses. Resolving that alias is the confirmation entry's job;
    #: storing the resolved id is what keeps the row readable without it.
    source_id: Mapped[str] = mapped_column(String(64))
    #: The pinned revision of the page (zh.wiktionary's ``oldid``). The same fact
    #: migration 0010 records as ``entry_source_evidence.source_revision`` for a CSV
    #: row; named for what it is here, because this row's line number indexes *this*
    #: revision's text and nothing else.
    page_revision: Mapped[str] = mapped_column(String(64))
    #: 1-based line number in that revision's wikitext. The database also requires
    #: ``typeof(line_number) = 'integer'``: SQLite's INTEGER affinity keeps text that
    #: does not look numeric as text, and would then order it after every real line.
    line_number: Mapped[int] = mapped_column(Integer)
    #: The line, byte for byte. Never trimmed, never truncated, and never rewritten
    #: into simplified characters: it is what the source said, and the confirmation
    #: step's verbatim test reads *this* column.
    raw_text: Mapped[str] = mapped_column(Text)
    #: The language section the line sits in, e.g. ``英語``. ``''`` means the language
    #: was not determined -- the honest answer for a page lead -- and never "some other
    #: language". A row whose heading path says another language cannot be stored.
    language_path: Mapped[str] = mapped_column(String(160), default="", server_default="")
    #: The headings that govern the line, joined by ``' > '``, e.g. ``英語 > 形容詞``.
    #: The line's own heading is included when it has one; a line that is itself a
    #: heading is described by ``pos_heading_*`` below.
    heading_path: Mapped[str] = mapped_column(String(200), default="", server_default="")
    #: The part of speech this line *is the heading for*, from the closed vocabulary
    #: ``CONCISE_MEANING_POS_KEYS``. Empty when the line is not a part-of-speech
    #: heading -- which is also what makes a non-heading (``發音``, ``詞源``) unable to
    #: pass as a basis for one.
    pos_heading_key: Mapped[str] = mapped_column(String(24), default="", server_default="")
    #: The heading as written, e.g. ``形容詞``. Kept beside the key for the same reason
    #: ``pos_label`` is kept beside ``pos_key``: the heading is the *evidence*, the key
    #: is the *mapping*, and Wiktionary spells one category ``形容詞`` on one page and
    #: ``形容词`` on another.
    pos_heading_text: Mapped[str] = mapped_column(String(40), default="", server_default="")
    #: SHA-256 of the whole pinned page text, lowercase hex. This is what makes the
    #: version a *content* fact rather than a number: a re-fetch of ``page_revision``
    #: that does not hash to this value is not the page these lines came from.
    page_text_sha256: Mapped[str] = mapped_column(String(64))
    #: SHA-256 of this row, from :func:`source_wikitext_line_sha256`. Recomputed by the
    #: confirmation step, so a row whose text was edited in place is detectable without
    #: the archive being present.
    line_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
