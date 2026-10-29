"""Exercise API contracts in an explicit private copy of the production database."""
import json
import os
from pathlib import Path
import sys

database = Path(sys.argv[1]).resolve()
assert database.name == 'verification.db' and database.parent.name == 'study-fixes-20261028'
assert str(database) != '/var/lib/shici/vocab.db'
os.environ.update(VOCAB_DATA_DIR=str(database.parent / 'verification-data'),
                  VOCAB_DATABASE_PATH=str(database), VOCAB_ENABLE_OCR='false')
sys.path.insert(0, sys.argv[2])

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.api.deps import get_current_user
from app.db import get_engine, get_session_factory
from app.main import app
from app.models import Lexicon, LexiconEntry, User, UserSettings, UserWordState

assert Path(get_engine().url.database).resolve() == database
factory = get_session_factory()
with factory() as session:
    owner = User(username='deployment-study-probe-owner', display_name='probe')
    other = User(username='deployment-study-probe-other', display_name='other')
    session.add_all([owner, other])
    session.flush()
    lexicon = Lexicon(name='Isolated study deployment probe', owner_user_id=owner.id, visibility='private')
    session.add(lexicon)
    session.flush()
    session.add(UserSettings(user_id=owner.id, daily_new_words=2, selected_lexicon_id=lexicon.id))
    ids = []
    for spelling in ('probe-retain', 'probe-deliberate'):
        entry = LexiconEntry(lexicon_id=lexicon.id, word=spelling, normalized_word=spelling,
                             source_meanings=['synthetic'], source_raw='')
        session.add(entry)
        session.flush()
        state = UserWordState(user_id=owner.id, lexicon_entry_id=entry.id)
        session.add(state)
        session.flush()
        ids.append(state.id)
    session.commit()
    with TestClient(app) as client:
        assert client.get('/api/study/today').status_code == 401
        app.dependency_overrides[get_current_user] = lambda: owner
        def queue():
            response = client.get('/api/study/today')
            assert response.status_code == 200, response.text
            return response.json()
        def answer(identifier, result):
            response = client.post(f'/api/study/word-states/{identifier}/review',
                json={'result': result, 'source': 'daily', 'review_type': 'recall'},
                headers={'Origin': 'http://testserver'})
            assert response.status_code == 200, response.text
        assert queue()['attempted_today'] == 0
        answer(ids[0], 'fail')
        payload = queue()
        assert payload['attempted_today'] == 1
        assert [item['word_state_id'] for item in payload['words']] == [ids[1], ids[0]]
        assert payload['words'][-1]['relearning_today'] is True
        answer(ids[1], 'fuzzy')
        answer(ids[0], 'know')
        answer(ids[1], 'know')
        payload = queue()
        assert payload['attempted_today'] == 2 and payload['words'] == []
        assert payload['daily_new_words']['consumed_today'] == 2
        app.dependency_overrides[get_current_user] = lambda: other
        assert queue()['attempted_today'] == 0
        assert client.post(f'/api/study/word-states/{ids[0]}/review',
            json={'result': 'know'}, headers={'Origin': 'http://testserver'}).status_code == 404
        app.dependency_overrides.clear()
print('STUDY_ISOLATED_PROBE_OK', json.dumps({'progress_restore': True,
      'fail_and_fuzzy_repeat': True, 'known_finishes_today': True,
      'new_word_budget_preserved': True, 'ownership_checks': True}))
