import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/phase29'))
import netem_display_quality as quality
from app.services.dictionary_extraction import display_extraction


def test_ipa_context_excludes_phoneme_discussion_and_inherits_weak_accent():
    text = '''==English==
===Pronunciation===
* {{a|RP}}
** {{IPA|en|/juː/|a=strong form}}
** {{IPA|en|/ju/|a=weak form}}
* Following /t/, {{IPAchar|/d/}} and {{IPAchar|/s/}} may coalesce.
* {{IPA|en|[n̩d]|a=unstressed}}
===Noun===
# {{IPA|en|/s/}} is a symbol.
==French==
===Pronunciation===
* {{IPA|fr|/y/}}
'''
    values = quality.pronunciations(text, 'you')
    assert [v['text'] for v in values] == ['juː', 'ju', 'n̩d']
    assert values[0]['display_labels'] == ['RP', 'strong form']
    assert values[1]['display_labels'] == ['RP', 'weak form']
    assert values[2]['notation'] == 'phonetic'


def test_homographs_and_optional_phones_are_preserved():
    values = quality.pronunciations('''==English==
===Etymology 1===
====Pronunciation====
* {{IPA|en|/lɛd/|a=RP}}
====Noun====
# metal
===Etymology 2===
====Pronunciation====
* {{IPA|en|/liːd/}}
====Verb====
# guide
''', 'lead')
    assert len({v['pronunciation_scope'] for v in values}) == 2
    assert quality.pronunciations('==English==\n===Pronunciation===\n* {{IPA|en|/ə(ɹ)/|a=weak form}}', 'or')[0]['text'] == 'ə(ɹ)'


def test_fragments_item_qualifiers_and_weak_reading_are_not_promoted():
    text='''==English==
===Pronunciation===
* {{IPA|en|/ɹɪˈkɔːd/|/ɹə-/|a=RP}}
* {{IPA|en|/kəmˈpəʉz/|[kəmˈpɐʉz]|a=AU|a2=some speakers}}
* {{IPA|en|/faːn/|a=Southern US,[[w:Monophthongization|monophthongal]]}}
'''
    values=quality.pronunciations(text,'record')
    assert 'ɹə-' not in [v['text'] for v in values]
    assert 'some speakers' in next(v for v in values if v['text']=='kəmˈpɐʉz')['display_labels']
    linked=next(v for v in values if v['text']=='faːn')
    assert linked['display_labels']==['Southern US','monophthongal']
    assert any('[[w:Monophthongization|monophthongal]]' in item['raw_template'] for item in linked['label_bindings'])
    weak=quality.choose_pronunciations(quality.pronunciations('==English==\n===Pronunciation===\n* {{IPA|en|/ə/|a=RP|q=weak form}}','a'))
    assert not any(v['representative'] for v in weak)


def test_same_pronunciation_heading_preserves_pos_specific_representatives():
    values=quality.pronunciations('''==English==
===Pronunciation===
* {{q|noun}}
** {{IPA|en|/ˈpɹɒspɛkt/|a=RP}}
* {{q|verb}}
** {{IPA|en|/pɹəˈspɛkt/|a=RP}}
''','prospect')
    assert all(v['representative'] for v in quality.choose_pronunciations(values))


def test_numbered_pronunciation_and_colon_bullets_preserve_explicit_context():
    values=quality.pronunciations('''==English==
===Pronunciation 1===
* {{a|en|RP}}
:: {{IPA|en|/pəˈliːs/}}
===Pronunciation 2===
: {{sense|Maori war-club}}
:* {{IPA|en|/ˈmɛɹi/}}
''','police')
    assert len(values)==2
    assert values[0]['display_labels']==['RP']
    assert values[1]['display_labels']==['Maori war-club']
    assert values[0]['pronunciation_scope']!=values[1]['pronunciation_scope']


def test_parent_labels_and_position_bound_qualifiers_do_not_cross_variants():
    values=quality.pronunciations('''==English==
===Pronunciation===
* {{IPA|en|/ju/|a=GA|q=weak form}}
** {{IPA|en|[jə]}}
* {{IPA|en|/ˈpɔɹtɹət/|a=GenAm}}, {{qualifier|rare}} {{IPA|en|/ˈpɔɹtɹeɪt/}}
''','you')
    assert values[1]['display_labels']==['GA','weak form']
    assert 'rare' not in values[2]['display_labels']
    assert 'rare' in values[3]['display_labels']
    last=quality.pronunciations('==English==\n===Pronunciation===\n* {{IPA|en|/ʃiː/|a=UK}} {{qualifier|rare}}','ski')
    assert 'rare' in last[0]['display_labels']
    assert not quality.choose_pronunciations(last)[0]['representative']


def test_restricted_source_readings_are_never_representatives():
    for restriction in ['rare','nonstandard','dialectally','archaic','spelling pronunciation']:
        values=quality.choose_pronunciations(quality.pronunciations(
            '==English==\n===Pronunciation===\n* {{IPA|en|/ʃiː/|a=UK|q='+restriction+'}}','ski'))
        assert not any(v['representative'] for v in values),restriction


def test_scope_binding_never_merges_different_english_uses():
    body = '<div><div><font class="grammar">verb</font></div><ol><li>study<div>学习</div></li><li>find out<div>學習</div></li></ol></div>'
    first = {'sense_path':'/div[1]/ol[1]/li[1]', 'node_paths':['/div[1]/ol[1]/li[1]/div[1]']}
    second = {'sense_path':'/div[1]/ol[1]/li[2]', 'node_paths':['/div[1]/ol[1]/li[2]/div[1]']}
    assert quality.sense_context(body, first) != quality.sense_context(body, second)
    assert quality.sense_context('<div><div><font class="grammar">noun</font></div>Christian feast<ol><li><div>復活節</div></li><li><div>复活节</div></li></ol></div>',
        {'sense_path':'/div[1]/ol[1]/li[1]/div[1]', 'node_paths':['/div[1]/ol[1]/li[1]/div[1]']})['glosses'] == ['Christian feast']


def test_api_profile_is_bound_to_original_payload_and_preserves_evidence():
    source = {'publisher':'WikDict'}
    s = {'text':'復活節','status':'source_verified','language':'en','pos_key':'noun','source':source,'locator':'x'}
    p = {'senses':[s],'pronunciations':[], 'originals':[], 'audit':{'version':'netem-source-audit-v1'}}
    profile = {'version':quality.VERSION, 'baseline_sha256':quality.digest(p),
               'senses':[dict(s,text='复活节')], 'pronunciations':[], 'held_pronunciations':[],
               'decisions':[], 'core_confirmation':False}
    p['learning_display'] = profile
    before = copy.deepcopy(p)
    result = display_extraction(SimpleNamespace(payload=p, lexicon_entry_id=1, parser_version='v1'))
    assert result['learning_display']['senses'][0]['text'] == '复活节'
    assert result['senses'][0]['text'] == '復活節'
    assert p == before
    p['senses'][0]['text'] = 'changed'
    result = display_extraction(SimpleNamespace(payload=p, lexicon_entry_id=1, parser_version='v1'))
    assert result.get('learning_display') is None
    assert result['display_quality_status'] == 'stale_profile'


def test_real_api_catalog_detail_and_study_share_projection_without_browse_state(world):
    from sqlalchemy import select, func
    from app.models import EntryDictionaryExtraction, UserWordState
    lexicon = world.lexicon('NETEM')
    _, entry_id = world.add_word('Easter', lexicon_id=lexicon)
    p = {'senses':[{'text':'復活節','language':'en','pos_key':'noun','status':'source_verified',
                    'locator':'s1','source':{'publisher':'WikDict'}}],
         'pronunciations':[], 'originals':[], 'audit':{'version':'netem-source-audit-v1'}}
    p['learning_display']={'version':quality.VERSION,'baseline_sha256':quality.digest(p),
        'core_confirmation':False,'senses':[dict(p['senses'][0],text='复活节')],
        'pronunciations':[], 'held_pronunciations':[]}
    with world.session() as session:
        session.add(EntryDictionaryExtraction(lexicon_entry_id=entry_id,parser_version='netem-rich-v1',
                    payload=p,payload_sha256=quality.digest(p)))
        session.commit()
        count=session.scalar(select(func.count()).select_from(UserWordState))
    assert world.client.post(f'/api/lexicons/{lexicon}/select').status_code==200
    detail=world.client.get(f'/api/words/entry/{entry_id}').json()['dictionary_extraction']
    listed=world.client.get('/api/words?catalog=true').json()['words'][0]['dictionary_extraction']
    assert detail==listed
    assert detail['learning_display']['senses'][0]['text']=='复活节'
    with world.session() as session:
        assert count==session.scalar(select(func.count()).select_from(UserWordState))
    study=world.client.get('/api/study/today').json()['words'][0]['dictionary_extraction']
    assert study==detail


def test_release_rejects_fabricated_projection_and_preserves_originals(world,tmp_path,monkeypatch):
    import netem_display_release as release
    import netem_rich_import as rich
    from sqlalchemy import select
    from app.models import EntryDictionaryExtraction, LexiconEntry
    import pytest
    lexicon=world.lexicon('NETEM');_,entry_id=world.add_word('Easter',lexicon_id=lexicon)
    original={'senses':[{'text':'復活節','status':'source_verified','language':'en','pos_key':'noun',
                        'locator':'s1','source':{'publisher':'Test','family':'other'}}],
              'pronunciations':[],'originals':[],'audit':{'version':'netem-source-audit-v1'}}
    profile=quality.projection({'payload':original,'word':'Easter'},None)
    after=copy.deepcopy(original);after['learning_display']=profile
    with world.session() as session:
        database=Path(session.bind.url.database)
        session.add(EntryDictionaryExtraction(lexicon_entry_id=entry_id,parser_version='netem-rich-v1',
            payload=original,payload_sha256=rich.digest(original)));session.commit()
    with rich.read_only(database) as c:
        entry=c.execute('select * from lexicon_entry where id=?',(entry_id,)).fetchone()
        baseline=rich.digest(rich.original_baseline(entry))
    monkeypatch.setattr(quality,'load_evidence',lambda folder:({},{}))
    monkeypatch.setattr(rich,'local_sources',lambda:({}, {}, {}))
    plan={'display_version':quality.VERSION,'core_confirmation':False,'lexicon_id':lexicon,
          'evidence_directory':'test-artifacts/test-evidence','source_files':{},
          'records':[{'entry_id':entry_id,'word':'Easter','baseline_sha256':baseline,
             'baseline_extraction_sha256':rich.digest(original),'payload':after,'payload_sha256':rich.digest(after)}]}
    path=rich.ROOT/'test-artifacts/netem-display-quality-20261009/test-release-plan.json'
    try:
        path.write_text(rich.canonical(plan),encoding='utf-8')
        assert release.validate_plan(database,path,rich.sha(path.read_bytes()))['records'][0]['payload']==after
        plan['records'][0]['payload']['learning_display']['senses'][0]['text']='伪造义项'
        plan['records'][0]['payload_sha256']=rich.digest(plan['records'][0]['payload'])
        path.write_text(rich.canonical(plan),encoding='utf-8')
        with pytest.raises(ValueError,match='locked source/context algorithm'):
            release.validate_plan(database,path,rich.sha(path.read_bytes()))
        with rich.read_only(database) as c:
            assert rich.digest(json.loads(c.execute('select payload from entry_dictionary_extraction where lexicon_entry_id=?',(entry_id,)).fetchone()[0]))==rich.digest(original)
    finally:
        path.unlink(missing_ok=True)


def test_authenticated_release_and_locked_receipt_preserve_later_review(make_world,monkeypatch):
    import uuid
    import pytest
    import netem_display_release as release
    import netem_rich_import as rich
    world=make_world('display-release-admin',role='admin')
    lexicon=world.lexicon('NETEM');state_id,entry_id=world.add_word('test',lexicon_id=lexicon)
    from app.models import EntryDictionaryExtraction
    original={'senses':[],'pronunciations':[],'originals':[]}
    with world.session() as session:
        database=Path(session.bind.url.database)
        session.add(EntryDictionaryExtraction(lexicon_entry_id=entry_id,parser_version=rich.PARSER_VERSION,
            payload=original,payload_sha256=rich.digest(original)));session.commit()
    with rich.read_only(database) as c:
        baseline=rich.digest(rich.original_baseline(c.execute('select * from lexicon_entry where id=?',(entry_id,)).fetchone()))
    after=copy.deepcopy(original);after['learning_display']=quality.projection({'payload':original,'word':'test'},None)
    monkeypatch.setattr(release,'check_code',lambda sha:None)
    monkeypatch.setattr(quality,'load_evidence',lambda folder:({},{}))
    monkeypatch.setattr(rich,'local_sources',lambda:({}, {}, {}))
    directory=rich.ROOT/'test-artifacts/netem-display-quality-20261009/authenticated-tests'/uuid.uuid4().hex
    directory.mkdir(parents=True);plan_path=directory/'plan.json'
    plan={'parser_version':rich.PARSER_VERSION,'display_version':quality.VERSION,'core_confirmation':False,
          'lexicon_id':lexicon,'source_files':{},'evidence_directory':'test-artifacts/test-evidence',
          'records':[{'entry_id':entry_id,'word':'test','baseline_sha256':baseline,
          'baseline_extraction_sha256':rich.digest(original),'payload':after,'payload_sha256':rich.digest(after)}]}
    plan_path.write_text(rich.canonical(plan),encoding='utf-8');checksum=rich.sha(plan_path.read_bytes())
    kwargs=dict(database=database,plan_path=plan_path,checksum=checksum,code_sha='a'*40,
                username=world.username,password='test-password-123')
    with pytest.raises(ValueError,match='password verification'):
        release.authenticated_execute(**(kwargs|{'password':'wrong'}),operation=directory/'bad-auth')
    assert not (directory/'bad-auth').exists()
    result=release.authenticated_execute(**kwargs,operation=directory/'apply')
    assert result['result']['changed']==1
    receipt=Path(result['receipt']);locked_sha=result['receipt_sha256']
    world.add_review(state_id)
    with rich.read_only(database) as c:
        before_tables=rich.table_fingerprints(c)
    tampered=json.loads(receipt.read_bytes());tampered['undo'][0]['before']['payload']='{"senses":["invented"]}'
    bad_receipt=directory/'tampered.json';bad_receipt.write_text(rich.canonical(tampered),encoding='utf-8')
    with pytest.raises(ValueError,match='receipt fingerprint'):
        release.validate_undo(database,plan_path,checksum,bad_receipt,locked_sha)
    with pytest.raises(ValueError,match='original baseline'):
        release.validate_undo(database,plan_path,checksum,bad_receipt,rich.sha(bad_receipt.read_bytes()))
    undone=release.authenticated_execute(**kwargs,operation=directory/'rollback',action='rollback',
                                        receipt=receipt,receipt_sha256=locked_sha)
    assert undone['result']['rolled_back']==1
    repeat=release.authenticated_execute(**kwargs,operation=directory/'rollback-repeat',action='rollback',
                                        receipt=receipt,receipt_sha256=locked_sha)
    assert repeat['result']['rolled_back']==0
    with rich.read_only(database) as c:
        assert rich.table_fingerprints(c)==before_tables


def test_report_output_cannot_overwrite_production_or_source(tmp_path):
    import pytest
    import netem_display_diagnosis as diagnosis
    import netem_rich_import as rich
    with pytest.raises(ValueError,match='protected data'):
        diagnosis.write_report(rich.ROOT/'data/vocab.db',{})
    path=tmp_path/'report.json'
    diagnosis.write_report(path,{'entries':1})
    original=path.read_bytes()
    diagnosis.write_report(path,{'entries':1})
    assert path.read_bytes()==original
    with pytest.raises(FileExistsError):diagnosis.write_report(path,{'entries':2})


def test_missing_english_context_never_merges_across_pos_or_usage():
    source={'family':'enwiktionary','publisher':'Test'}
    base={'text':'变化','locator':'same-source-line','language':'en','status':'source_verified','source':source}
    payload={'senses':[dict(base,pos_key='noun'),dict(base,pos_key='verb'),
                       dict(base,pos_key='verb',display_usage_labels=['比喻'])],
             'pronunciations':[],'originals':[],'audit':{'version':'netem-source-audit-v1'}}
    profile=quality.projection({'payload':payload,'word':'change'},None)
    assert len(profile['senses'])==3
