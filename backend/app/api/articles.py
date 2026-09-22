from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, ensure_user_settings
from app.api.helpers import article_dict, lookup_dict, word_dict_from_view
from app.models import Article, ArticleWordExposure, ArticleWordLookup, LexiconEntry, UserWordState
from app.schemas import ArticleGenerateRequest, ArticleJudgeRequest, ArticleLookupRequest
from app.services.ai import AIProviderError, DeepSeekProvider
from app.services.reading import (
    add_lookup_to_wordbook,
    complete_article,
    lookup_article_word,
    select_target_words,
    translate_article,
    validate_actual_used_words,
)
from app.services.userdata import WordView, load_user_article, not_found

router = APIRouter(prefix="/api/articles", tags=["articles"])


@router.get("")
def list_articles(user: CurrentUser, session: SessionDep) -> list[dict[str, object]]:
    """Only this user's articles."""
    articles = session.scalars(
        select(Article)
        .where(Article.user_id == user.id)
        .order_by(Article.created_at.desc())
        .limit(50)
    ).all()
    return [article_dict(article) for article in articles]


@router.get("/{article_id}")
def get_article(article_id: int, user: CurrentUser, session: SessionDep) -> dict[str, object]:
    """Article detail for its owner, or 404 for everyone else.

    This is one of the endpoints the Phase 0 audit flagged: it used to load any
    article by id. Access now goes through an owner-scoped lookup, so another
    user's id is indistinguishable from a nonexistent one.
    """
    article = load_user_article(session, user, article_id, with_children=True)
    payload = article_dict(article, include_lookups=True)
    if article.completed:
        rows = session.execute(
            select(UserWordState, LexiconEntry)
            .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
            .join(
                ArticleWordExposure,
                ArticleWordExposure.lexicon_entry_id == LexiconEntry.id,
            )
            .where(
                UserWordState.user_id == user.id,
                ArticleWordExposure.article_id == article.id,
            )
        ).all()
        contexts = {
            entry_id: context
            for entry_id, context in session.execute(
                select(ArticleWordExposure.lexicon_entry_id, ArticleWordExposure.context).where(
                    ArticleWordExposure.article_id == article.id
                )
            ).all()
        }
        payload["quiz_words"] = [
            {
                **word_dict_from_view(WordView(state=state, entry=entry)),
                "context": contexts.get(entry.id, ""),
            }
            for state, entry in rows
        ]
    return payload


@router.post("/generate")
async def generate_article(
    payload: ArticleGenerateRequest, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """Generate an article from **this user's** words."""
    views = select_target_words(session, user, payload.target_count)
    if not views:
        raise HTTPException(409, "词库还是空的，请先导入一些单词")
    settings = ensure_user_settings(session, user)
    length = payload.length or settings.article_length
    targets = [view.entry.word for view in views]
    try:
        result = await DeepSeekProvider().generate_article(targets, length)
    except AIProviderError as error:
        raise HTTPException(503, str(error)) from error
    actual = validate_actual_used_words(targets, result.actual_used_words, result.article)
    article = Article(
        user_id=user.id,
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
def finish_article(
    article_id: int, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    article = load_user_article(session, user, article_id)
    complete_article(session, user, article)
    return get_article(article_id, user, session)


@router.post("/{article_id}/judge")
async def judge_article_word(
    article_id: int,
    payload: ArticleJudgeRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, object]:
    article = load_user_article(session, user, article_id)
    exposure = session.scalar(
        select(ArticleWordExposure).where(
            ArticleWordExposure.article_id == article.id,
            ArticleWordExposure.word_id == payload.word_id,
        )
    )
    if exposure is None:
        raise not_found("本篇文章中没有这个测试词")
    entry = (
        session.get(LexiconEntry, exposure.lexicon_entry_id)
        if exposure.lexicon_entry_id
        else None
    )
    state = session.scalar(
        select(UserWordState).where(
            UserWordState.user_id == user.id,
            UserWordState.lexicon_entry_id == exposure.lexicon_entry_id,
        )
    )
    if entry is None or state is None:
        raise not_found("本篇文章中没有这个测试词")
    try:
        result = await DeepSeekProvider().judge_meaning(
            entry.word, payload.user_meaning, exposure.context, entry.source_meanings
        )
    except AIProviderError as error:
        raise HTTPException(503, str(error)) from error
    return result.model_dump()


@router.post("/{article_id}/lookup")
async def lookup_word(
    article_id: int,
    payload: ArticleLookupRequest,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, object]:
    article = load_user_article(session, user, article_id)
    try:
        lookup = await lookup_article_word(session, user, article, payload.word, DeepSeekProvider())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except AIProviderError as error:
        raise HTTPException(503, str(error)) from error
    return lookup_dict(lookup)


@router.post("/{article_id}/translate")
async def translate(
    article_id: int, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    article = load_user_article(session, user, article_id, with_children=True)
    try:
        await translate_article(session, user, article, DeepSeekProvider())
    except AIProviderError as error:
        raise HTTPException(503, str(error)) from error
    return article_dict(article, include_lookups=True)


@router.post("/{article_id}/lookups/{lookup_id}/add-word")
def add_lookup_word(
    article_id: int, lookup_id: int, user: CurrentUser, session: SessionDep
) -> dict[str, object]:
    """Move a looked-up word into **this user's** private lexicon."""
    article = load_user_article(session, user, article_id)
    lookup = session.scalar(
        select(ArticleWordLookup).where(
            ArticleWordLookup.id == lookup_id,
            ArticleWordLookup.article_id == article.id,
        )
    )
    if lookup is None:
        raise not_found("查词记录不存在")
    entry = add_lookup_to_wordbook(session, user, lookup)
    state = session.scalar(
        select(UserWordState).where(
            UserWordState.user_id == user.id,
            UserWordState.lexicon_entry_id == entry.id,
        )
    )
    return {
        "lookup": lookup_dict(lookup),
        "word": word_dict_from_view(WordView(state=state, entry=entry)),
    }
