import asyncio
from datetime import UTC, datetime, timedelta


def test_actual_used_words_must_be_targets_and_present_in_article() -> None:
    """Unchanged V1.1 rule: trust neither the model nor casing."""
    from app.services.reading import validate_actual_used_words

    actual = validate_actual_used_words(
        ["retain", "subtle", "derive"],
        ["Retain", "derive", "invented", "retain"],
        "We retain useful ideas, but the conclusion is subtle.",
    )
    assert actual == ["retain"]


def test_weak_words_are_selected_before_other_words(world) -> None:
    from app.services.reading import select_target_words

    world.add_word("ordinary", status="new")
    world.add_word("fragile", status="weak")
    state_id, _entry = world.add_word("retain", status="known")
    world.add_review(state_id, "know")

    with world.session() as session:
        selected = select_target_words(session, world.reload_user(), limit=3)
    assert selected[0].entry.word == "fragile"


def test_target_words_come_only_from_the_acting_user(world, make_world) -> None:
    """A shared lexicon must not leak another user's words into the selection."""
    from app.services.reading import select_target_words

    other = make_world("reading-other")
    try:
        other.add_word("intruder", status="weak")
        world.add_word("mine", status="new")

        with world.session() as session:
            selected = select_target_words(session, world.reload_user(), limit=10)
        words = {view.entry.word for view in selected}
        assert words == {"mine"}, words
    finally:
        other.client.__exit__(None, None, None)


def test_article_completion_creates_explicit_exposure_once(world) -> None:
    from app.models import ArticleWordExposure

    state_id, entry_id = world.add_word("retain", anchor="保留")
    article_id = world.add_article(
        title="Memory and cities",
        content="Cities retain traces of the people who built them.",
        target_words=["retain"],
        actual_used_words=["retain"],
    )

    with world.session() as session:
        from app.models import Article

        article = session.get(Article, article_id)
        from app.services.reading import complete_article

        complete_article(session, world.reload_user(), article)
        complete_article(session, world.reload_user(), article)

    with world.session() as session:
        from app.models import UserWordState

        state = session.get(UserWordState, state_id)
        assert state.context_exposure == 1
        exposures = session.query(ArticleWordExposure).all()
        assert len(exposures) == 1
        assert exposures[0].article_id == article_id
        assert exposures[0].lexicon_entry_id == entry_id
        assert "retain" in exposures[0].context.lower()


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


def test_article_lookup_uses_local_word_and_persists_history(world) -> None:
    from app.models import ArticleWordLookup

    world.add_word(
        "retain",
        anchor="保留",
        source_meanings=["保留", "保持"],
        source_raw="retain v. 保留；保持",
    )
    article_id = world.add_article(title="Memory", content="We retain what matters.")
    ai = FakeReadingAI()

    with world.session() as session:
        from app.models import Article

        article = session.get(Article, article_id)
        result = asyncio.run(
            __import__(
                "app.services.reading", fromlist=["lookup_article_word"]
            ).lookup_article_word(session, world.reload_user(), article, "retain", ai)
        )

    assert result.meaning == "保留"
    assert result.source == "wordbook"
    assert ai.lookup_calls == 0, "a word already in the user's lexicon must not call the AI"
    with world.session() as session:
        stored = session.query(ArticleWordLookup).filter_by(article_id=article_id).all()
        assert len(stored) == 1
        assert stored[0].source == "wordbook"


def test_article_lookup_and_translation_are_cached(world) -> None:
    from app.services.reading import lookup_article_word, translate_article

    article_id = world.add_article(
        title="Nuance", content="A subtle change altered the result."
    )
    ai = FakeReadingAI()

    with world.session() as session:
        from app.models import Article

        article = session.get(Article, article_id)
        user = world.reload_user()
        first = asyncio.run(lookup_article_word(session, user, article, "subtle", ai))
        second = asyncio.run(lookup_article_word(session, user, article, "Subtle", ai))
        asyncio.run(translate_article(session, user, article, ai))
        asyncio.run(translate_article(session, user, article, ai))
        session.refresh(article)

    assert first.id == second.id
    assert ai.lookup_calls == 1
    assert ai.translation_calls == 1
    assert article.translation.startswith("Nuance（译文）")


def test_add_lookup_to_wordbook_preserves_article_source_and_exposure(world) -> None:
    """An added word goes to the caller's private lexicon, not a global pool."""
    from app.models import ArticleWordExposure, Lexicon, LexiconEntry, UserWordState
    from app.services.reading import add_lookup_to_wordbook, lookup_article_word

    article_id = world.add_article(
        title="Nuance", content="A subtle change altered the result."
    )
    with world.session() as session:
        from app.models import Article

        article = session.get(Article, article_id)
        user = world.reload_user()
        lookup = asyncio.run(lookup_article_word(session, user, article, "subtle", FakeReadingAI()))
        entry = add_lookup_to_wordbook(session, user, lookup)
        same_entry = add_lookup_to_wordbook(session, user, lookup)
        entry_id = entry.id

    assert entry.id == same_entry.id

    with world.session() as session:
        entry = session.get(LexiconEntry, entry_id)
        lexicon = session.get(Lexicon, entry.lexicon_id)
        state = session.query(UserWordState).filter_by(lexicon_entry_id=entry_id).one()

        assert lexicon.owner_user_id == world.user_id, "must land in the user's own lexicon"
        assert lexicon.visibility == "private"
        assert entry.source_raw == "A subtle change altered the result."
        assert entry.source_meanings == []
        assert entry.default_anchor == "微妙的"
        assert entry.possible_issue is True
        assert state.user_id == world.user_id
        assert state.possible_issue is True
        assert state.context_exposure == 1
        article_exposures = (
            session.query(ArticleWordExposure)
            .filter_by(article_id=article_id)
            .all()
        )
        assert len(article_exposures) == 1


def test_reading_history_is_attributed_to_the_acting_user(world) -> None:
    from app.models import HistoryEvent
    from app.services.reading import lookup_article_word

    article_id = world.add_article(title="Nuance", content="A subtle change altered it.")
    with world.session() as session:
        from app.models import Article

        article = session.get(Article, article_id)
        asyncio.run(
            lookup_article_word(session, world.reload_user(), article, "subtle", FakeReadingAI())
        )

    with world.session() as session:
        events = session.query(HistoryEvent).filter_by(event_type="article_word_lookup").all()
        assert events, "the lookup must be recorded"
        assert all(event.user_id == world.user_id for event in events)


def test_article_selection_prefers_due_and_failed_words(world) -> None:
    """The V1.1 priority order is intact, now over the user's own states."""
    from app.services.reading import select_target_words

    world.add_word("ordinary", status="new")
    failed_state, _ = world.add_word("failed", status="learning")
    world.add_review(failed_state, "fail")

    with world.session() as session:
        selected = select_target_words(session, world.reload_user(), limit=5)
    assert selected[0].entry.word == "failed"


def test_oldest_reviewed_words_rank_below_unreviewed_ones(world) -> None:
    from app.services.reading import select_target_words

    fresh_state, _ = world.add_word("alpha", status="new")
    world.add_word("beta", status="new")
    world.add_review(fresh_state, "know")

    with world.session() as session:
        selected = select_target_words(session, world.reload_user(), limit=5)
    # "beta" has never been reviewed, so it is prioritised over the reviewed word.
    assert selected[0].entry.word == "beta"


def test_stale_review_timestamp_still_selects_for_reading(world) -> None:
    """A word reviewed long ago remains eligible."""
    from app.models import UserWordState
    from app.services.reading import select_target_words

    state_id, _ = world.add_word("stale", status="known")
    with world.session() as session:
        state = session.get(UserWordState, state_id)
        state.last_review = datetime.now(UTC) - timedelta(days=60)
        session.add(state)
        session.commit()

    with world.session() as session:
        selected = select_target_words(session, world.reload_user(), limit=5)
    assert "stale" in {view.entry.word for view in selected}
