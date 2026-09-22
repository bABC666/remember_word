from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Article,
    ArticleWordExposure,
    ArticleWordLookup,
    HistoryEvent,
    Lexicon,
    LexiconEntry,
    User,
    UserLexicon,
    UserWordState,
)
from app.services.ai.base import AIProvider
from app.services.userdata import WordView, accessible_lexicon_ids

#: Reading-side rules are unchanged from V1.1. What changed in V1.2 is that every
#: selection and every write is scoped to the acting user.


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


def select_target_words(session: Session, user: User, limit: int = 20) -> list[WordView]:
    """Pick words to build an article from, **for this user only**.

    Priority mirrors V1.1 (weak first, then previously failed, then recently
    added, then new) but the candidate set comes from the user's own
    ``UserWordState`` rows, scoped in SQL.
    """
    now = datetime.now(UTC)
    priority = case(
        (UserWordState.status == "weak", 0),
        (UserWordState.consecutive_failures > 0, 1),
        (UserWordState.first_seen >= now - timedelta(days=2), 2),
        (UserWordState.status == "new", 3),
        else_=4,
    )
    rows = session.execute(
        select(UserWordState, LexiconEntry)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(UserWordState.user_id == user.id)
        .order_by(
            priority,
            UserWordState.last_review.is_not(None),
            UserWordState.last_review,
            UserWordState.context_exposure,
        )
        .limit(limit)
    ).all()
    return [WordView(state=state, entry=entry) for state, entry in rows]


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


def complete_article(session: Session, user: User, article: Article) -> Article:
    """Mark the article complete and record exposures for the acting user.

    Exposures are linked to the user's own words, so completing an article can
    never touch another user's ``context_exposure``.
    """
    if article.completed:
        return article
    now = datetime.now(UTC)
    actual = {item.casefold() for item in article.actual_used_words}
    rows = session.execute(
        select(UserWordState, LexiconEntry)
        .join(LexiconEntry, LexiconEntry.id == UserWordState.lexicon_entry_id)
        .where(
            UserWordState.user_id == user.id,
            LexiconEntry.normalized_word.in_([item.casefold() for item in actual]),
        )
    ).all()
    for state, entry in rows:
        if entry.word.casefold() not in actual:
            continue
        _record_exposure(session, article, state, entry, now=now)
        state.context_exposure += 1
    article.completed = True
    article.completed_at = now
    session.commit()
    session.refresh(article)
    return article


def _record_exposure(
    session: Session,
    article: Article,
    state: UserWordState,
    entry: LexiconEntry,
    *,
    now: datetime,
) -> ArticleWordExposure:
    """Create or refresh the link between an article and one of the user's words.

    Keyed on the lexicon entry so it works for words with no legacy ``word`` row,
    which is the case for every word a new user adds from an article.
    """
    exposure = session.scalar(
        select(ArticleWordExposure).where(
            ArticleWordExposure.article_id == article.id,
            ArticleWordExposure.lexicon_entry_id == entry.id,
        )
    )
    context = _context(article.content, entry.word)
    if exposure is None:
        exposure = ArticleWordExposure(
            article_id=article.id,
            word_id=state.legacy_word_id,
            lexicon_entry_id=entry.id,
            context=context,
            first_exposed_at=now,
            last_exposed_at=now,
        )
        session.add(exposure)
    else:
        exposure.exposure_count += 1
        exposure.last_exposed_at = now
        if context and not exposure.context:
            exposure.context = context
    return exposure


def normalize_lookup_word(surface: str) -> str:
    match = re.search(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", surface)
    if not match:
        raise ValueError("请选择一个英文单词")
    value = match.group(0).replace("’", "'").casefold()
    if value.endswith("'s") and len(value) > 2:
        value = value[:-2]
    return value


def _local_entry(
    session: Session, user: User, normalized: str
) -> tuple[LexiconEntry, UserWordState] | None:
    """Look the word up in the lexicons this user may read.

    Only the user's own learning state and the shared lexicon content are
    consulted, so a word from someone else's private lexicon is never revealed
    and never triggers an AI call on their behalf.
    """
    lexicon_ids = accessible_lexicon_ids(session, user)
    if not lexicon_ids:
        return None
    entry = session.scalar(
        select(LexiconEntry)
        .where(
            LexiconEntry.lexicon_id.in_(lexicon_ids),
            LexiconEntry.normalized_word == normalized,
        )
        .limit(1)
    )
    if entry is None:
        return None
    state = session.scalar(
        select(UserWordState).where(
            UserWordState.user_id == user.id,
            UserWordState.lexicon_entry_id == entry.id,
        )
    )
    if state is None:
        return None
    return entry, state


async def lookup_article_word(
    session: Session,
    user: User,
    article: Article,
    surface: str,
    provider: AIProvider,
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

    local = _local_entry(session, user, normalized)
    if local is not None:
        entry, state = local
        meaning = state.anchor_override or entry.default_anchor
        lookup = ArticleWordLookup(
            article_id=article.id,
            surface=surface,
            normalized_word=normalized,
            phonetic=entry.phonetic,
            part_of_speech=entry.part_of_speech,
            meaning=meaning or "；".join(entry.source_meanings) or entry.source_raw,
            explanation=state.semantic_note or entry.semantic_note,
            context=context,
            source="wordbook",
            added_word_id=state.legacy_word_id,
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
            user_id=user.id,
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
    session: Session, user: User, article: Article, provider: AIProvider
) -> Article:
    if article.translation:
        return article
    result = await provider.translate_article(article.title, article.content)
    article.translation = result.translation
    article.translated_at = datetime.now(UTC)
    article.translation_ai_raw_json = result.model_dump()
    session.add(
        HistoryEvent(
            user_id=user.id,
            event_type="article_translated",
            entity_type="article",
            entity_id=article.id,
            payload={"model_output_validated": True},
        )
    )
    session.commit()
    session.refresh(article)
    return article


def personal_lexicon(session: Session, user: User) -> Lexicon:
    """The user's own vocabulary book, created on first use.

    Words added while reading go here rather than into a global pool, so each
    user's additions stay theirs.
    """
    existing = session.scalar(
        select(Lexicon)
        .where(
            Lexicon.owner_user_id == user.id,
            Lexicon.source_type == "personal",
        )
        .order_by(Lexicon.id)
        .limit(1)
    )
    if existing is not None:
        return existing

    lexicon = Lexicon(
        owner_user_id=user.id,
        name="我的生词本",
        description="从阅读中收藏的生词",
        visibility="private",
        source_type="personal",
    )
    session.add(lexicon)
    session.flush()
    if session.scalar(
        select(UserLexicon).where(
            UserLexicon.user_id == user.id, UserLexicon.lexicon_id == lexicon.id
        )
    ) is None:
        session.add(UserLexicon(user_id=user.id, lexicon_id=lexicon.id, enabled=True))
    return lexicon


def add_lookup_to_wordbook(
    session: Session, user: User, lookup: ArticleWordLookup
) -> LexiconEntry:
    """Add a looked-up word to **this user's** private lexicon.

    Creates the user's own ``UserWordState`` and an exposure link to their
    article. It never writes to a shared lexicon, and never creates a global
    word row on someone else's behalf.
    """
    from app.services.userdata import get_or_create_word_state

    lexicon = personal_lexicon(session, user)
    normalized = lookup.normalized_word.casefold()
    entry = session.scalar(
        select(LexiconEntry).where(
            LexiconEntry.lexicon_id == lexicon.id,
            LexiconEntry.normalized_word == normalized,
        )
    )
    if entry is None:
        entry = LexiconEntry(
            lexicon_id=lexicon.id,
            word=lookup.normalized_word,
            normalized_word=normalized,
            phonetic=lookup.phonetic,
            part_of_speech=lookup.part_of_speech,
            source_meanings=[],
            # The article sentence is the source; the AI explanation is learning
            # data, and the word is flagged for human review.
            source_raw=lookup.context,
            default_anchor=lookup.meaning,
            semantic_note=lookup.explanation,
            possible_issue=True,
        )
        session.add(entry)
        session.flush()
        lexicon.entry_count = (
            session.scalar(
                select(func.count())
                .select_from(LexiconEntry)
                .where(LexiconEntry.lexicon_id == lexicon.id)
            )
            or 0
        )

    state = get_or_create_word_state(session, user, entry)
    state.possible_issue = True
    if not state.notes:
        state.notes = "从阅读文章加入；中文释义由 AI 辅助生成，请在学习时核对。"

    article = session.get(Article, lookup.article_id)
    if article is not None and not article.is_owned_by(user.id):
        # Defensive: the caller already resolved the article by owner, so this
        # can only happen if ownership changed underneath us.
        raise LookupError("文章不存在")

    now = datetime.now(UTC)
    exposure = session.scalar(
        select(ArticleWordExposure).where(
            ArticleWordExposure.article_id == lookup.article_id,
            ArticleWordExposure.lexicon_entry_id == entry.id,
        )
    )
    if exposure is None:
        session.add(
            ArticleWordExposure(
                article_id=lookup.article_id,
                word_id=state.legacy_word_id,
                lexicon_entry_id=entry.id,
                context=lookup.context,
                first_exposed_at=now,
                last_exposed_at=now,
            )
        )
        # Flush so a second call in the same transaction finds this row instead
        # of adding a duplicate exposure.
        session.flush()
        state.context_exposure += 1

    lookup.added_word_id = state.legacy_word_id
    session.add(
        HistoryEvent(
            user_id=user.id,
            event_type="article_word_added",
            entity_type="article",
            entity_id=lookup.article_id,
            payload={
                "lookup_id": lookup.id,
                "lexicon_id": lexicon.id,
                "lexicon_entry_id": entry.id,
                "article_owner": article.user_id if article else None,
            },
        )
    )
    session.commit()
    session.refresh(entry)
    return entry
