"""Verify real routes in an isolated migrated copy, using a local fake AI."""
import json
import os
from pathlib import Path
import sys

database = Path(sys.argv[1]).resolve()
assert database.name == 'verification.db' and database.parent.name == 'reading-review-20261030'
os.environ.update(VOCAB_DATA_DIR=str(database.parent / 'verification-data'),
                  VOCAB_DATABASE_PATH=str(database), VOCAB_ENABLE_OCR='false')
sys.path.insert(0, sys.argv[2])
from fastapi.testclient import TestClient
from app.api import articles
from app.api.deps import get_current_user
from app.db import get_engine, get_session_factory
from app.main import app
from app.models import Article, Lexicon, LexiconEntry, User, UserSettings, UserWordState
from app.services.ai.base import ArticleGeneration, ArticleTranslation, WordLookupResult

assert Path(get_engine().url.database).resolve() == database


class FakeAI:
    async def generate_article(self, targets, length):
        return ArticleGeneration(title='Isolated test', article=(' '.join(targets) + ' ') * 30, actual_used_words=targets)

    async def lookup_word(self, word, context):
        return WordLookupResult(normalized_word=word, meaning='银行' if 'money' in context else '河岸', explanation=context)

    async def translate_article(self, title, content):
        return ArticleTranslation(translation='他把钱存进银行，然后沿着河岸散步，最后回家了。',
                                  sentences=['他把钱存进银行。', '他沿着河岸走。'])


articles.DeepSeekProvider = FakeAI
with get_session_factory()() as session:
    user = User(username='reading-release-probe-owner')
    other = User(username='reading-release-probe-other')
    session.add_all([user, other])
    session.flush()
    lexicon = Lexicon(owner_user_id=user.id, name='Isolated reading probe', visibility='private')
    session.add(lexicon)
    session.flush()
    session.add(UserSettings(user_id=user.id, daily_new_words=4, selected_lexicon_id=lexicon.id))
    identifiers = {}
    for spelling in ('fragile', 'uncertain', 'known', 'bank'):
        entry = LexiconEntry(lexicon_id=lexicon.id, word=spelling, normalized_word=spelling,
                             source_meanings=['synthetic'], source_raw='')
        session.add(entry)
        session.flush()
        state = UserWordState(user_id=user.id, lexicon_entry_id=entry.id)
        session.add(state)
        session.flush()
        identifiers[spelling] = state.id
    article = Article(user_id=user.id, title='Two banks', content='He left money at the bank. He walked along the bank.',
                      target_words=['bank'], actual_used_words=['bank'])
    session.add(article)
    session.commit()
    article_id = article.id
    with TestClient(app) as client:
        assert client.get('/api/study/today').status_code == 401
        app.dependency_overrides[get_current_user] = lambda: user
        def post(route, body=None):
            response = client.post(route, json=body, headers={'Origin': 'http://testserver'})
            assert response.status_code == 200, response.text
            return response.json()
        def answer(spelling, result):
            return post(f'/api/study/word-states/{identifiers[spelling]}/review', {'result': result})
        def queue():
            response = client.get('/api/study/today')
            assert response.status_code == 200, response.text
            return response.json()
        answer('fragile', 'fail')
        answer('uncertain', 'fuzzy')
        answer('known', 'know')
        generated = post('/api/articles/generate', {'target_count': 4, 'length': 300})
        assert generated['target_words'][:2] == ['fragile', 'uncertain']
        assert 'known' not in generated['target_words']
        answer('fragile', 'know')
        pending = next(word for word in queue()['words'] if word['word'] == 'fragile')
        assert pending['practice_remaining'] == 1
        answer('fragile', 'know')
        answer('uncertain', 'know')
        assert all(word['word'] not in ('fragile', 'uncertain', 'known') for word in queue()['words'])
        one = post(f'/api/articles/{article_id}/lookup', {'word': 'bank', 'sentence_index': 0})
        two = post(f'/api/articles/{article_id}/lookup', {'word': 'bank', 'sentence_index': 1})
        assert one['meaning'] == '银行' and two['meaning'] == '河岸' and one['id'] != two['id']
        translated = post(f'/api/articles/{article_id}/translate')
        assert translated['sentence_translations'] == ['他把钱存进银行。', '他沿着河岸走。']
        assert len(translated['lookup_history']) == 2
        assert post(f'/api/articles/{article_id}/lookup', {'word': 'bank', 'sentence_index': 1})['id'] == two['id']
        app.dependency_overrides[get_current_user] = lambda: other
        assert client.get(f'/api/articles/{article_id}').status_code == 404
        app.dependency_overrides.clear()
print('READING_REVIEW_PROBE_OK', json.dumps({'context_cache': True, 'sentence_translation': True,
      'difficult_word_priority': True, 'weighted_practice': True, 'ownership': True, 'paid_ai_calls': 0}))
