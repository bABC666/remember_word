from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.models import Article, ArticleWordExposure, Word


def validate_actual_used_words(
    target_words: list[str], reported_words: list[str], content: str
) -> list[str]:
    """Trust neither the model nor casing: keep only target words present in the article."""
    target_map = {word.casefold(): word for word in target_words}
    actual: list[str] = []
    for candidate in reported_words:
        canonical = target_map.get(candidate.casefold())
        if (
            canonical
            and re.search(rf"\b{re.escape(canonical)}\b", content, re.IGNORECASE)
            and canonical not in actual
        ):
            actual.append(canonical)
    return actual


def select_target_words(session: Session, limit: int = 20) -> list[Word]:
    now = datetime.now(UTC)
    priority = case(
        (Word.status == "weak", 0),
        (Word.consecutive_failures > 0, 1),
        (Word.first_seen >= now - timedelta(days=2), 2),
        (Word.status == "new", 3),
        else_=4,
    )
    return list(
        session.scalars(
            select(Word)
            .order_by(
                priority, Word.last_review.is_not(None), Word.last_review, Word.context_exposure
            )
            .limit(limit)
        ).all()
    )


def _context(content: str, word: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", content)
    return next(
        (
            sentence.strip()
            for sentence in sentences
            if re.search(rf"\b{re.escape(word)}\b", sentence, re.IGNORECASE)
        ),
        "",
    )


def complete_article(session: Session, article_id: int) -> Article:
    article = session.get(Article, article_id)
    if article is None:
        raise LookupError("Article not found")
    if article.completed:
        return article
    now = datetime.now(UTC)
    actual = {item.casefold() for item in article.actual_used_words}
    words = session.scalars(
        select(Word).where(Word.word.in_(list(article.actual_used_words)))
    ).all()
    for word in words:
        if word.word.casefold() not in actual:
            continue
        exposure = ArticleWordExposure(
            article_id=article.id,
            word_id=word.id,
            context=_context(article.content, word.word),
            first_exposed_at=now,
            last_exposed_at=now,
        )
        session.add(exposure)
        word.context_exposure += 1
    article.completed = True
    article.completed_at = now
    session.commit()
    session.refresh(article)
    return article
