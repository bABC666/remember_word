import copy
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('netem_source_audit', ROOT / 'tools/phase29/netem_source_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def candidate(text='四月', pos='pron', raw_pos='pronoun'):
    return {'text': text, 'pos_key': pos, 'pos_label': '代词', 'pos_raw': raw_pos,
            'language': 'en', 'locator': 'wikdict:1:2', 'status': 'pending',
            'reason': 'upstream_proper_name_pronoun_requires_review',
            'raw_text': 'fourth month of the Gregorian calendar<div>四月</div>',
            'source': {'family': 'wikdict', 'body_sha256': 'a' * 64,
                       'original_file_sha256': 'b' * 64, 'publisher': 'WikDict'}}


def decision(sense, **changes):
    return {'entry_id': 123, 'word': 'April', 'i': 0, 'locator': sense['locator'],
            'body_sha256': sense['source']['body_sha256'], 'text': sense['text'],
            'meaning': 'accept', 'pos': 'unknown', 'scope': 'word_translation',
            'note': '固定原文第四个月与四月对应；原 pronoun 不可靠，词性留空。', **changes}


def test_meaning_can_be_verified_independently_of_defective_pos():
    sense = candidate()
    original = copy.deepcopy(sense)
    result = audit.audit_sense(sense, decision(sense), entry_id=123, word='April', index=0)
    assert result['text'] == '四月'
    assert result['status'] == 'source_verified'
    assert result['pos_key'] == ''
    assert result['source_pos_key'] == 'pron'
    assert result['pos_raw'] == 'pronoun'
    assert result['quality_review']['core_confirmation'] is False
    assert sense == original


def test_semantic_defect_is_excluded_without_erasing_evidence():
    sense = candidate('酸', 'prep', 'preposition')
    result = audit.audit_sense(sense, decision(sense, word='above', meaning='reject',
        note='over/on top of 与酸不对应，原片段保留供对照。'), entry_id=123, word='above', index=0)
    assert result['status'] == 'rejected'
    assert result['text'] == '酸' and result['raw_text'] == sense['raw_text']


def test_stale_review_or_non_english_acceptance_refuses():
    sense = candidate()
    with pytest.raises(ValueError, match='binding'):
        audit.audit_sense(sense, decision(sense, text='五月'), entry_id=123, word='April', index=0)
    sense['language'] = 'fr'
    with pytest.raises(ValueError, match='English'):
        audit.audit_sense(sense, decision(sense), entry_id=123, word='April', index=0)


def test_shared_translation_stays_word_level_not_fake_individual_senses():
    sense = candidate('跑', 'verb', 'verb')
    sense['reason'] = 'shared_translation_multiple_glosses'
    sense['has_bound_sense'] = False
    result = audit.audit_sense(sense, decision(sense, word='run', pos='accept'),
                              entry_id=123, word='run', index=0)
    assert result['semantic_scope'] == 'word_translation'
    assert result['text'] == '跑' and result['pos_key'] == 'verb'


@pytest.mark.parametrize('family', ['wikdict', 'zhwiktionary', 'enwiktionary'])
def test_missing_accent_does_not_hide_valid_pinned_source_ipa(family):
    value = candidate()
    value.update(text='ˈeɪ.pɹəl', reason='unlabelled_variant_requires_review')
    value['source']['family'] = family
    result = audit.audit_pronunciation(value)
    assert result['status'] == 'source_verified'
    assert result['text'] == 'ˈeɪ.pɹəl'
    assert result['accent_status'] == 'unspecified'
    assert result['quality_review']['core_confirmation'] is False


def test_license_hold_cannot_be_promoted_by_semantic_acceptance():
    sense = candidate()
    sense.update(reason='license_marker_requires_review')
    result = audit.audit_sense(sense, decision(sense), entry_id=123, word='April', index=0)
    assert result['status'] == 'pending'
    assert result['reason'] == 'license_marker_requires_review'


def test_english_citation_context_keeps_pos_and_rejects_other_language_lines():
    text = '==English==\n===Verb===\n# put something down\n====Translations====\n*: Mandarin: 放置\n==French==\n===Noun===\n# unrelated'
    assert audit.english_context(text, 3) == ('verb', 'Verb', 2)
    assert audit.english_context(text, 5) == ('verb', 'Verb', 2)
    with pytest.raises(ValueError, match='English'):
        audit.english_context(text, 8)


def test_nested_english_glosses_sharing_one_chinese_node_are_word_translation():
    from app.services.rich_dictionary_parser import parse_wikdict
    raw = ('<font class="grammar">noun</font><ol><li><ol>'
           '<li>physical perception</li><li>meaning or reason</li><li>natural ability</li>'
           '</ol><div>意義</div></li></ol>')
    sense = parse_wikdict('sense', raw)['senses'][0]
    sense.update(locator='test:sense:1', source=candidate()['source'])
    sense['source']['family'] = 'wikdict'
    assert sense['has_bound_sense'] is True
    result = audit.audit_sense(sense, decision(sense, word='sense', scope='sense', pos='accept'),
                              entry_id=123, word='sense', index=0)
    assert result['status'] == 'source_verified'
    assert result['semantic_scope'] == 'word_translation'
    assert len(result['source_glosses']) == 3


@pytest.mark.parametrize('raw_pos, key', [('{{=n=|英|play}}', 'noun'),
                                        ('{{=v=|en|play}}', 'verb'),
                                        ('{{=a=|英|cold}}', 'adj')])
def test_explicit_old_english_pos_marker_is_retained_after_audit(raw_pos, key):
    sense = candidate('剧', key, raw_pos)
    result = audit.audit_sense(sense, decision(sense, pos='accept'), entry_id=123, word='April', index=0)
    assert result['pos_key'] == key


def test_review_correction_requires_exact_previous_decision_and_complete_coverage():
    directory = ROOT/'test-artifacts/netem-source-audit-tests'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    sense = candidate()
    record = {'entry_id':123, 'payload':{'senses':[sense]}}
    original = decision(sense, meaning='pending')
    review = directory/'review.json'
    review.write_text(json.dumps({'review_kind':audit.REVIEW_KIND, 'core_confirmation':False,
                                 'decisions':[original]}, ensure_ascii=False), encoding='utf-8')
    correction = directory/'correction.json'
    replacement = decision(sense, previous_decision_sha256=audit.rich.digest(original))
    correction.write_text(json.dumps({'review_kind':audit.REVIEW_KIND, 'core_confirmation':False,
                                     'decisions':[replacement]}, ensure_ascii=False), encoding='utf-8')
    result, _ledger = audit.load_reviews([review], [record], correction)
    assert result[(123,0)]['meaning'] == 'accept'
    replacement['previous_decision_sha256'] = '0'*64
    correction.write_text(json.dumps({'review_kind':audit.REVIEW_KIND, 'core_confirmation':False,
                                     'decisions':[replacement]}, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match='stale'):
        audit.load_reviews([review], [record], correction)
    with pytest.raises(ValueError, match='coverage'):
        audit.load_reviews([review], [], None)
