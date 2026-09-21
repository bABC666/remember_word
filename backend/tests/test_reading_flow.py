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
