import asyncio
from datetime import UTC, datetime, timedelta


def test_actual_used_words_must_be_targets_and_present_in_article() -> None:
    from app.services.reading import validate_actual_used_words

    actual = validate_actual_used_words(
        ["retain", "subtle", "derive"],
        ["Retain", "derive", "invented", "retain"],
        "We retain useful ideas, but the conclusion is subtle.",
    )
    assert actual == ["retain"]


def test_weak_words_are_selected_before_other_words(session) -> None:
    from app.models import Word
    from app.services.reading import select_target_words

    session.add_all(
        [
            Word(word="ordinary", status="new", source_raw="ordinary"),
            Word(word="fragile", status="weak", source_raw="fragile"),
            Word(
                word="retain",
                status="known",
                source_raw="retain",
                last_review=datetime.now(UTC) - timedelta(days=30),
            ),
        ]
    )
    session.commit()
    assert select_target_words(session, limit=3)[0].word == "fragile"


def test_article_completion_creates_explicit_exposure_once(session) -> None:
    from app.models import Article, ArticleWordExposure, Word
    from app.services.reading import complete_article

    word = Word(word="retain", source_raw="retain", source_meanings=["保留"])
    article = Article(
        title="Memory and cities",
        content="Cities retain traces of the people who built them.",
        target_words=["retain"],
        actual_used_words=["retain"],
    )
    session.add_all([word, article])
    session.commit()

    complete_article(session, article.id)
    complete_article(session, article.id)
    session.refresh(word)
    assert word.context_exposure == 1
    exposure = session.query(ArticleWordExposure).one()
    assert exposure.word_id == word.id
    assert exposure.article_id == article.id
    assert "retain" in exposure.context.lower()


class FakeReadingAI:
    def __init__(self) -> None:
        self.lookup_calls = 0
        self.translation_calls = 0

    async def lookup_word(self, word: str, context: str):
        from app.services.ai.base import WordLookupResult

        self.lookup_calls += 1
        return WordLookupResult(
            normalized_word=word.lower(),
            phonetic="/ˈsʌtəl/",
            part_of_speech="adj.",
            meaning="微妙的",
            explanation=f"在句中指不明显但重要的差异：{context[:30]}",
        )

    async def translate_article(self, title: str, content: str):
        from app.services.ai.base import ArticleTranslation

        self.translation_calls += 1
        return ArticleTranslation(translation=f"{title}（译文）\n\n{content}（中文翻译）")


def test_article_lookup_uses_local_word_and_persists_history(session) -> None:
    from app.models import Article, ArticleWordLookup, Word
    from app.services.reading import lookup_article_word

    word = Word(
        word="retain",
        phonetic="/rɪˈteɪn/",
        part_of_speech="v.",
        source_meanings=["保留", "保持"],
        source_raw="retain v. 保留；保持",
        anchor="保留",
    )
    article = Article(title="Memory", content="We retain what matters.")
    session.add_all([word, article])
    session.commit()
    ai = FakeReadingAI()

    result = asyncio.run(lookup_article_word(session, article, "retain", ai))

    assert result.meaning == "保留"
    assert result.source == "wordbook"
    assert ai.lookup_calls == 0
    assert session.query(ArticleWordLookup).count() == 1


def test_article_lookup_and_translation_are_cached(session) -> None:
    from app.models import Article
    from app.services.reading import lookup_article_word, translate_article

    article = Article(title="Nuance", content="A subtle change altered the result.")
    session.add(article)
    session.commit()
    ai = FakeReadingAI()

    first = asyncio.run(lookup_article_word(session, article, "subtle", ai))
    second = asyncio.run(lookup_article_word(session, article, "Subtle", ai))
    asyncio.run(translate_article(session, article, ai))
    asyncio.run(translate_article(session, article, ai))

    assert first.id == second.id
    assert ai.lookup_calls == 1
    assert ai.translation_calls == 1
    assert article.translation.startswith("Nuance（译文）")


def test_add_lookup_to_wordbook_preserves_article_source_and_exposure(session) -> None:
    from app.models import Article, ArticleWordExposure
    from app.services.reading import add_lookup_to_wordbook, lookup_article_word

    article = Article(title="Nuance", content="A subtle change altered the result.")
    session.add(article)
    session.commit()
    lookup = asyncio.run(lookup_article_word(session, article, "subtle", FakeReadingAI()))

    word = add_lookup_to_wordbook(session, lookup)
    same_word = add_lookup_to_wordbook(session, lookup)

    assert word.id == same_word.id
    assert word.source_raw == "A subtle change altered the result."
    assert word.source_meanings == []
    assert word.anchor == "微妙的"
    assert word.possible_issue is True
    assert word.context_exposure == 1
    assert session.query(ArticleWordExposure).count() == 1
