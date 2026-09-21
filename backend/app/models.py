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

    exposures: Mapped[list[ArticleWordExposure]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    review_events: Mapped[list[ReviewEvent]] = relationship(back_populates="article")


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
