from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Word(Base):
    __tablename__ = "word"

    id: Mapped[int] = mapped_column(primary_key=True)
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


class ArticleWordExposure(Base):
    __tablename__ = "article_word_exposure"
    __table_args__ = (UniqueConstraint("article_id", "word_id", name="uq_article_word"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("article.id", ondelete="CASCADE"), index=True
    )
    word_id: Mapped[int] = mapped_column(ForeignKey("word.id", ondelete="CASCADE"), index=True)
    context: Mapped[str] = mapped_column(Text, default="")
    exposure_count: Mapped[int] = mapped_column(Integer, default=1)
    first_exposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_exposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    article: Mapped[Article] = relationship(back_populates="exposures")
    word: Mapped[Word] = relationship(back_populates="exposures")


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
    word_id: Mapped[int] = mapped_column(ForeignKey("word.id", ondelete="CASCADE"), index=True)
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
