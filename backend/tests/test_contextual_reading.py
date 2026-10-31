import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Article, ArticleWordLookup, ReviewEvent, User, UserWordState
from app.services.ai.base import AIProviderError, ArticleTranslation, WordLookupResult
from app.services.reading import lookup_article_word, select_target_words, translate_article
from app.services.sentences import aligned_translations, article_sentences


class ContextAI:
    def __init__(self):
        self.calls = []
        self.translations = 0

    async def lookup_word(self, word, context):
        self.calls.append(context)
        return WordLookupResult(normalized_word=word, meaning="银行" if "money" in context else "河岸",
                                explanation=context)

    async def translate_article(self, title, content):
        self.translations += 1
        return ArticleTranslation(translation="他把钱存进银行，然后沿着河岸散步，最后回家了。",
                                  sentences=["他把钱存进银行。", "他沿着河岸走。"])


def test_same_word_is_explained_for_each_clicked_sentence_and_cached(world):
    world.add_word("bank", anchor="银行")
    article_id = world.add_article(title="Banks", content="He left money at the bank. He walked along the bank.")
    ai = ContextAI()
    with world.session() as session:
        article = session.get(Article, article_id)
        user = world.reload_user()
        old = asyncio.run(lookup_article_word(session, user, article, "bank", ai))
        assert old.source == "wordbook"
        first = asyncio.run(lookup_article_word(session, user, article, "bank", ai, sentence_index=0))
        second = asyncio.run(lookup_article_word(session, user, article, "bank", ai, sentence_index=1))
        repeated = asyncio.run(lookup_article_word(session, user, article, "Bank", ai, sentence_index=1))
        assert first.meaning == "银行" and second.meaning == "河岸"
        assert first.id != second.id and repeated.id == second.id
        assert len(ai.calls) == 2
        assert session.scalar(select(ArticleWordLookup).where(ArticleWordLookup.id == old.id)).meaning == "银行"
        with pytest.raises(ValueError):
            asyncio.run(lookup_article_word(session, user, article, "money", ai, sentence_index=1))
        with pytest.raises(ValueError):
            asyncio.run(lookup_article_word(session, user, article, "bank", ai, sentence_index=5))
        assert len(ai.calls) == 2


def test_translation_has_exact_alignment_and_legacy_text_is_preserved(world):
    article_id = world.add_article(title="Banks", content="He left money at the bank. He walked along the bank.")
    ai = ContextAI()
    with world.session() as session:
        article = session.get(Article, article_id)
        article.translation = "旧的整篇翻译，不应被错误地逐行配对。"
        session.commit()
        asyncio.run(translate_article(session, world.reload_user(), article, ai))
        assert aligned_translations(article) == ["他把钱存进银行。", "他沿着河岸走。"]
        assert article.translation_ai_raw_json["previous_translation"].startswith("旧的")
        asyncio.run(translate_article(session, world.reload_user(), article, ai))
        assert ai.translations == 1


def test_misaligned_translation_does_not_overwrite_existing_text(world):
    class BadAI(ContextAI):
        async def translate_article(self, title, content):
            return ArticleTranslation(translation="这是一段故意不与句子数量一致的译文，用于验证失败保留。", sentences=["合并了两句话"])
    article_id = world.add_article(title="Two", content="One sentence. Another sentence.")
    with world.session() as session:
        article = session.get(Article, article_id)
        article.translation = "原有译文"
        session.commit()
        with pytest.raises(AIProviderError):
            asyncio.run(translate_article(session, world.reload_user(), article, BadAI()))
        session.refresh(article)
        assert article.translation == "原有译文" and aligned_translations(article) == []


def test_selection_prioritizes_todays_fuzzy_and_fail_and_excludes_last_known(world):
    fresh, _ = world.add_word("new-word")
    old, _ = world.add_word("old-fail")
    fuzzy, _ = world.add_word("today-fuzzy")
    failed, _ = world.add_word("today-fail")
    known, _ = world.add_word("now-known")
    for state_id, result in [(old, "fail"), (fuzzy, "fuzzy"), (failed, "fail"), (known, "fail"), (known, "know")]:
        world.add_review(state_id, result)
    with world.session() as session:
        entry_id = session.get(UserWordState, old).lexicon_entry_id
        for event in session.scalars(select(ReviewEvent).where(ReviewEvent.lexicon_entry_id == entry_id)):
            event.timestamp = datetime.now(UTC) - timedelta(days=2)
        session.commit()
        values = [view.state.id for view in select_target_words(session, world.reload_user(), 10)]
    assert set(values[:2]) == {fuzzy, failed}
    assert values.index(old) < values.index(fresh)
    assert known not in values


def test_sentence_boundaries_preserve_quotes_decimals_and_abbreviations():
    units = article_sentences('Dr. May paid 3.14 dollars. “Really?” she asked.\n\nYes!')
    assert [item['text'] for item in units] == ['Dr. May paid 3.14 dollars.', '“Really?” she asked.', 'Yes!']
    assert units[-1]['paragraph'] == 1


def test_migration_preserves_all_lookup_fields_and_allows_sentence_keys(alembic_database):
    assert alembic_database('upgrade', '0016_dictionary_extraction').returncode == 0
    engine = create_engine(f"sqlite:///{alembic_database.database.as_posix()}")
    with Session(engine) as session:
        user = User(username="migration-context-owner")
        session.add(user)
        session.flush()
        article = Article(user_id=user.id, title="Bank", content="The bank holds money. The bank is muddy.", target_words=[], actual_used_words=[])
        session.add(article)
        session.flush()
        lookup = ArticleWordLookup(article_id=article.id, surface="bank", normalized_word="bank", meaning="银行",
                                   context="The bank holds money.", source="ai", ai_raw_json={"proof": "retained"})
        session.add(lookup)
        session.commit()
        article_id = article.id
    with engine.connect() as connection:
        before = connection.exec_driver_sql('select * from article_word_lookup order by id').fetchall()
    result = alembic_database('upgrade', 'head')
    assert result.returncode == 0, result.stderr
    with engine.connect() as connection:
        assert connection.exec_driver_sql('select * from article_word_lookup order by id').fetchall() == before
        assert connection.exec_driver_sql('pragma foreign_key_check').fetchall() == []
    with Session(engine) as session:
        session.add(ArticleWordLookup(article_id=article_id, surface="bank", normalized_word="bank", meaning="河岸",
                                     context="The bank is muddy.", source="ai"))
        session.commit()
    engine.dispose()
