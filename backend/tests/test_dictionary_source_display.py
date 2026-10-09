from app.models import EntryDictionaryExtraction
from app.services.dictionary_extraction import display_extraction


def test_audited_meaning_without_pos_is_usable_and_rejected_fragments_stay_in_evidence():
    value = display_extraction(EntryDictionaryExtraction(parser_version='netem-rich-v1', payload={
        'audit': {'version': 'netem-source-audit-v1', 'core_confirmation': False},
        'senses': [
            {'text': '四月', 'language': 'en', 'status': 'source_verified', 'pos_key': '',
             'pos_raw': 'pronoun', 'quality_review': {'core_confirmation': False}},
            {'text': '酸', 'language': 'en', 'status': 'rejected', 'reason': 'source_semantic_conflict'},
            {'text': '模板', 'language': 'en', 'status': 'pending', 'reason': 'unexpanded_template'}],
        'pronunciations': [{'text': 'ˈeɪ.pɹəl', 'language': 'en', 'status': 'source_verified'}]}))
    assert [s['text'] for s in value['senses']] == ['四月']
    assert value['senses'][0]['pos_key'] == ''
    assert value['audit_status'] == 'automated_source_verified'
    assert value['pending_count'] == 1 and value['excluded_count'] == 1
    assert {s['text'] for s in value['pending_values']} == {'酸', '模板'}
    assert value['pronunciations'][0]['text'] == 'ˈeɪ.pɹəl'
    assert value['status'] == 'unconfirmed_source_extraction'
