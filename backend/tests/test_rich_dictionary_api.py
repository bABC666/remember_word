from sqlalchemy import func, select

from app.models import EntryDictionaryExtraction, LexiconEntry, UserWordState


def test_catalog_detail_and_study_share_unconfirmed_source_groups_without_state_write(world):
    lexicon = world.lexicon('NETEM')
    with world.session() as s:
        entry = LexiconEntry(lexicon_id=lexicon, word='play', normalized_word='play',
                             source_meanings=['旧释义'])
        s.add(entry)
        s.flush()
        entry_id = entry.id
        s.add(EntryDictionaryExtraction(lexicon_entry_id=entry_id, parser_version='netem-rich-v1',
            payload_sha256='a' * 64, payload={'senses': [
                {'pos_key': 'verb', 'pos_label': '动词', 'text': '玩', 'language': 'en',
                 'status': 'extracted', 'locator': 'test:12', 'source': {'publisher': 'WikDict'}},
                {'pos_key': 'noun', 'pos_label': '名词', 'text': '剧', 'language': 'en',
                 'status': 'extracted', 'locator': 'test:14', 'source': {'publisher': 'WikDict'}},
                {'pos_key': 'noun', 'text': '酸', 'language': 'en', 'status': 'pending',
                 'reason': 'upstream_error'}], 'pronunciations': []}))
        s.commit()
        before = s.scalar(select(func.count()).select_from(UserWordState))
    world.client.post(f'/api/lexicons/{lexicon}/select')
    listed = world.client.get('/api/words?catalog=true').json()['words'][0]
    detail = world.client.get(f'/api/words/entry/{entry_id}').json()
    assert detail['dictionary_extraction'] == listed['dictionary_extraction']
    assert [x['text'] for x in detail['dictionary_extraction']['senses']] == ['玩', '剧']
    assert detail['dictionary_extraction']['pending_count'] == 1
    assert detail['dictionary_extraction']['pending_values'][0]['reason'] == 'upstream_error'
    assert detail['concise_meanings'] == []
    assert detail['source_meanings'] == ['旧释义']
    with world.session() as s:
        assert s.scalar(select(func.count()).select_from(UserWordState)) == before
    studied = world.client.get('/api/study/today').json()['words'][0]
    assert studied['dictionary_extraction'] == detail['dictionary_extraction']


def test_missing_structured_source_keeps_old_response_usable(world):
    world.add_word('old')
    word = world.client.get('/api/words').json()['words'][0]
    assert word['dictionary_extraction'] is None
