from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import ReviewEvent, UserWordState


@pytest.mark.parametrize('initial,required', [('know', 0), ('fuzzy', 1), ('fail', 2)])
def test_first_answer_and_saved_practice_plan_survive_queue_reload(world, initial, required):
    identifier, _ = world.add_word('weighted-' + initial)
    def answer(result):
        response = world.client.post(f'/api/study/word-states/{identifier}/review',
            json={'result': result, 'source': 'daily', 'review_type': 'recall'})
        assert response.status_code == 200
    def queue():
        return world.client.get('/api/study/today').json()
    answer(initial)
    with world.session() as session:
        state = session.get(UserWordState, identifier)
        before = (state.status, state.next_review_at, state.recall_fail, state.consecutive_failures)
    for remaining in reversed(range(1, required + 1)):
        for _ in range(2):
            payload = queue()
            item = next(item for item in payload['words'] if item['word_state_id'] == identifier)
            assert item['practice_remaining'] == remaining
            assert payload['attempted_today'] == 1
        answer('know')
    assert all(item['word_state_id'] != identifier for item in queue()['words'])
    with world.session() as session:
        state = session.get(UserWordState, identifier)
        assert (state.status, state.next_review_at, state.recall_fail, state.consecutive_failures) == before


def test_repeated_failures_require_at_most_three_successful_recalls(world):
    identifier, _ = world.add_word('repeated-weighted')
    for _ in range(4):
        assert world.client.post(f'/api/study/word-states/{identifier}/review', json={'result': 'fail'}).status_code == 200
    item = world.client.get('/api/study/today').json()['words'][0]
    assert item['practice_required'] == item['practice_remaining'] == 3
    for _ in range(3):
        world.client.post(f'/api/study/word-states/{identifier}/review', json={'result': 'know'})
    assert world.client.get('/api/study/today').json()['words'] == []


def test_old_failures_decay_out_of_practice_weight(world):
    from app.services.relearning import practice_plans
    identifier, entry = world.add_word('old-pressure')
    world.add_review(identifier, 'fail')
    with world.session() as session:
        for event in session.scalars(select(ReviewEvent).where(ReviewEvent.user_id == world.user_id)):
            event.timestamp = datetime.now(UTC) - timedelta(days=8)
        session.commit()
        plan = practice_plans(session, world.user_id, [entry], datetime.now(UTC))[entry]
        assert plan.pressure == plan.remaining == 0


def test_repeated_reading_does_not_count_as_repeated_recall(world):
    identifier, _ = world.add_word('exposure-only')
    article = world.add_article(title='Reading', content='An exposure-only example.',
                               target_words=['exposure-only'], actual_used_words=['exposure-only'])
    world.client.post(f'/api/articles/{article}/complete')
    world.client.post(f'/api/articles/{article}/complete')
    with world.session() as session:
        state = session.get(UserWordState, identifier)
        assert state.context_exposure == 1 and state.status == 'new' and state.recall_success == 0


def test_context_quiz_cannot_promote_a_word_repeatedly_on_the_same_day(world):
    identifier, _ = world.add_word('cross-source-practice')
    route = f'/api/study/word-states/{identifier}/review'
    assert world.client.post(route, json={'result': 'know'}).status_code == 200
    for _ in range(4):
        assert world.client.post(route, json={'result': 'know', 'source': 'reading',
                                              'review_type': 'context_meaning'}).status_code == 200
    with world.session() as session:
        state = session.get(UserWordState, identifier)
        assert state.status == 'familiar' and state.recall_success == 1
