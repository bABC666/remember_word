from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Article, ArticleWordExposure, ArticleWordLookup, HistoryEvent, Word
from app.services.ai.base import AIProvider


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


def normalize_lookup_word(surface: str) -> str:
    match = re.search(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", surface)
    if not match:
        raise ValueError("请选择一个英文单词")
    value = match.group(0).replace("’", "'").casefold()
    if value.endswith("'s") and len(value) > 2:
        value = value[:-2]
    return value


async def lookup_article_word(
    session: Session, article: Article, surface: str, provider: AIProvider
) -> ArticleWordLookup:
    normalized = normalize_lookup_word(surface)
    cached = session.scalar(
        select(ArticleWordLookup).where(
            ArticleWordLookup.article_id == article.id,
            ArticleWordLookup.normalized_word == normalized,
        )
    )
    if cached is not None:
        return cached

    context = _context(article.content, normalized)
    if not context:
        raise ValueError("所选单词不在这篇文章中")
    local_word = session.scalar(
        select(Word).where(func.lower(Word.word) == normalized).limit(1)
    )
    if local_word is not None:
        meaning = local_word.anchor or "；".join(local_word.source_meanings)
        lookup = ArticleWordLookup(
            article_id=article.id,
            surface=surface,
            normalized_word=normalized,
            phonetic=local_word.phonetic,
            part_of_speech=local_word.part_of_speech,
            meaning=meaning or local_word.source_raw,
            explanation=local_word.semantic_note,
            context=context,
            source="wordbook",
            added_word_id=local_word.id,
        )
    else:
        result = await provider.lookup_word(normalized, context)
        lookup = ArticleWordLookup(
            article_id=article.id,
            surface=surface,
            normalized_word=result.normalized_word.casefold(),
            phonetic=result.phonetic,
            part_of_speech=result.part_of_speech,
            meaning=result.meaning,
            explanation=result.explanation,
            context=context,
            source="ai",
            ai_raw_json=result.model_dump(),
        )
    session.add(lookup)
    session.add(
        HistoryEvent(
            event_type="article_word_lookup",
            entity_type="article",
            entity_id=article.id,
            payload={"word": normalized, "source": lookup.source},
        )
    )
    session.commit()
    session.refresh(lookup)
    return lookup


async def translate_article(
    session: Session, article: Article, provider: AIProvider
) -> Article:
    if article.translation:
        return article
    result = await provider.translate_article(article.title, article.content)
    article.translation = result.translation
    article.translated_at = datetime.now(UTC)
    article.translation_ai_raw_json = result.model_dump()
    session.add(
        HistoryEvent(
            event_type="article_translated",
            entity_type="article",
            entity_id=article.id,
            payload={"model_output_validated": True},
        )
    )
    session.commit()
    session.refresh(article)
    return article


def add_lookup_to_wordbook(session: Session, lookup: ArticleWordLookup) -> Word:
    word = session.scalar(
        select(Word).where(func.lower(Word.word) == lookup.normalized_word.casefold()).limit(1)
    )
    if word is None:
        word = Word(
            word=lookup.normalized_word,
            phonetic=lookup.phonetic,
            part_of_speech=lookup.part_of_speech,
            source_meanings=[],
            source_raw=lookup.context,
            anchor=lookup.meaning,
            semantic_note=lookup.explanation,
            status="new",
            possible_issue=True,
            notes="从阅读文章加入；中文释义由 AI 辅助生成，请在学习时核对。",
        )
        session.add(word)
        session.flush()

    exposure = session.scalar(
        select(ArticleWordExposure).where(
            ArticleWordExposure.article_id == lookup.article_id,
            ArticleWordExposure.word_id == word.id,
        )
    )
    if exposure is None:
        now = datetime.now(UTC)
        session.add(
            ArticleWordExposure(
                article_id=lookup.article_id,
                word_id=word.id,
                context=lookup.context,
                first_exposed_at=now,
                last_exposed_at=now,
            )
        )
        word.context_exposure += 1
    lookup.added_word_id = word.id
    session.add(
        HistoryEvent(
            event_type="article_word_added",
            entity_type="word",
            entity_id=word.id,
            payload={"article_id": lookup.article_id, "lookup_id": lookup.id},
        )
    )
    session.commit()
    session.refresh(word)
    return word
