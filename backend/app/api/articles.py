from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import article_dict, word_dict
from app.db import get_session
from app.models import AppSetting, Article, ArticleWordExposure, Word
from app.schemas import ArticleGenerateRequest, ArticleJudgeRequest
from app.services.ai import AIProviderError, DeepSeekProvider
from app.services.reading import (
    complete_article,
    select_target_words,
    validate_actual_used_words,
)

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get("")
def list_articles(session: Session = Depends(get_session)) -> list[dict[str, object]]:
    articles = session.scalars(select(Article).order_by(Article.created_at.desc()).limit(50)).all()
    return [article_dict(article) for article in articles]


@router.get("/{article_id}")
def get_article(article_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    article = session.scalar(
        select(Article).where(Article.id == article_id).options(selectinload(Article.exposures))
    )
    if article is None:
        raise HTTPException(404, "文章不存在")
    payload = article_dict(article)
    if article.completed:
        word_ids = [item.word_id for item in article.exposures]
        words = session.scalars(select(Word).where(Word.id.in_(word_ids))).all() if word_ids else []
        contexts = {item.word_id: item.context for item in article.exposures}
        payload["quiz_words"] = [
            {**word_dict(word), "context": contexts.get(word.id, "")} for word in words
        ]
    return payload


@router.post("/generate")
async def generate_article(
    payload: ArticleGenerateRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    words = select_target_words(session, payload.target_count)
    if not words:
        raise HTTPException(409, "词库还是空的，请先导入一些单词")
    setting = session.get(AppSetting, "article_length")
    length = payload.length or (int(setting.value) if setting else 650)
    targets = [word.word for word in words]
    try:
        result = await DeepSeekProvider().generate_article(targets, length)
    except AIProviderError as error:
        raise HTTPException(503, str(error)) from error
    actual = validate_actual_used_words(targets, result.actual_used_words, result.article)
    article = Article(
        title=result.title,
        content=result.article,
        target_words=targets,
        actual_used_words=actual,
        ai_raw_json=result.model_dump(),
    )
    session.add(article)
    session.commit()
    session.refresh(article)
    return article_dict(article)


@router.post("/{article_id}/complete")
def finish_article(article_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        article = complete_article(session, article_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return get_article(article.id, session)


@router.post("/{article_id}/judge")
async def judge_article_word(
    article_id: int, payload: ArticleJudgeRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    word = session.get(Word, payload.word_id)
    exposure = session.scalar(
        select(ArticleWordExposure).where(
            ArticleWordExposure.article_id == article_id,
            ArticleWordExposure.word_id == payload.word_id,
        )
    )
    if word is None or exposure is None:
        raise HTTPException(404, "本篇文章中没有这个测试词")
    try:
        result = await DeepSeekProvider().judge_meaning(
            word.word, payload.user_meaning, exposure.context, word.source_meanings
        )
    except AIProviderError as error:
        raise HTTPException(503, str(error)) from error
    return result.model_dump()
