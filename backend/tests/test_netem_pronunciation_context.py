"""Regression expectations independent of the candidate, from pinned real pages."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/phase29'))
import netem_display_quality as quality

FIXTURE=json.loads((Path(__file__).parent/'fixtures/netem_pronunciation_context_20261009.json').read_bytes())


def values(word):
    return quality.choose_pronunciations(quality.pronunciations(FIXTURE['words'][word]['text'],word))


def test_plant_local_accent_overrides_the_multi_region_enpr_parent():
    parsed=values('plant')
    north=next(v for v in parsed if v['text']=='plant' and v['notation']=='phonemic')
    american=next(v for v in parsed if v['text']=='plænt' and v['notation']=='phonemic')
    assert north['display_accent'] not in {'US','UK,US'}
    assert not north['representative']
    assert not any('US' in label.split(',') for label in north['display_labels'])
    assert 'Northern England' in north['display_labels']
    assert american['display_accent']=='US'
    assert american['representative']


def test_grow_decodes_linked_ga_label_and_folds_regional_variants():
    parsed=values('grow')
    ga=next(v for v in parsed if v['text']=='ˈɡɹoʊ')
    assert ga['display_accent']=='US' and ga['representative']
    assert 'Standard Canadian' in ga['display_labels']
    assert all(not v['representative'] for v in parsed if v['text'] in {'ˈɡɹɜʊ̯','ˈɡɹəʊ̯','ˈɡɹɘʊ̯'})


@pytest.mark.parametrize('word,ipa,qualifier',[
    ('forget','fəˈɡɛt','less commonly'),('forget','fɚˈɡɛt','less commonly'),
    ('grocer','ˈɡɹoʊ.səɹ','sometimes proscribed'),
    ('tomato','təˈmeɪ.toʊ','colloquial'),('potato','pəˈteɪ.toʊ','colloquial'),
    ('wit','wɪt','before yod')])
def test_following_ipachar_qualifier_never_attaches_to_preceding_ipa(word,ipa,qualifier):
    target=next(v for v in values(word) if v['text']==ipa)
    assert not any(qualifier in label for label in target['display_labels'])
    assert not any(qualifier in label for label in target.get('qualifier_labels',[]))


def test_parent_usage_and_indexed_qualifier_are_preserved_in_real_sources():
    and_values=values('and')
    raised=next(v for v in and_values if v['text']=='ˈeə̯nd')
    assert 'GA' in raised['display_labels']
    assert 'non-æ-raising' not in raised['display_labels']
    assert 'æ-raising' in raised['display_labels']
    compose=next(v for v in values('compose') if v['text']=='kəmˈpɐʉz')
    assert 'some speakers' in compose['display_labels']


def test_complete_ipachar_alternatives_are_not_classified_as_confirmed_fragments():
    for word,text in [('forget','fɔːˈɡɛt'),('forget','fɔɹˈɡɛt'),('grocer','ˈɡɹoʊ.ʃəɹ')]:
        lines=[{'line':n,'raw_text':line} for n,line in quality.pronunciation_context_lines(FIXTURE['words'][word]['text']) if text in line]
        assert lines and not quality.confirmed_fragment_context(word,text,lines)


def test_qualifier_for_following_audio_does_not_change_the_previous_ipa():
    values=quality.pronunciations('==English==\n===Pronunciation===\n* {{IPA|en|/ɡɹoʊ/|a=GA}}, {{q|rare}} {{audio|en|variant.ogg}}','grow')
    assert 'rare' not in values[0]['display_labels']


def test_actual_positional_accent_parameters_remain_separate():
    target=next(v for v in values('comparable') if v['text']=='ˈkɑmpɚəbəl')
    assert target['display_accent']=='US' and target['representative']
    assert 'GA' in target['accent_labels'] and 'Atlantic Canada' in target['accent_labels']


def test_actual_environment_labels_refine_instead_of_replacing_region():
    targets=[v for v in values('with') if v['text'] in {'wɪð','wɪθ'} and 'preconsonantal' in v['display_labels']]
    assert any('RP' in v['accent_labels'] for v in targets)
    assert all('final' not in v['accent_labels'] for v in targets)


def test_actual_child_regions_keep_parent_phonological_feature():
    parsed=values('bank')
    basic=next(v for v in parsed if v['text']=='ˈbæŋk' and v['notation']=='phonemic')
    raised=next(v for v in parsed if v['text']=='ˈbeɪ̯ŋk' and v['notation']=='phonemic')
    assert 'non-æ-tensing' in basic['feature_labels']
    assert 'æ-tensing' in raised['feature_labels']


def test_actual_wrapped_ga_and_feature_are_separate_axes():
    target=next(v for v in values('fan') if v['text']=='fæn' and v['notation']=='phonemic')
    assert 'GA' in target['accent_labels']
    assert 'non-æ-tensing' in target['feature_labels']
    assert 'US' in target['accent_families'] and target['representative']


def test_actual_th_fronted_variant_does_not_displace_unqualified_reading():
    parsed=values('thin')
    assert next(v for v in parsed if v['text']=='ˈθɪn')['representative']
    assert not next(v for v in parsed if v['text']=='ˈfɪn')['representative']


def test_actual_whitespace_language_parameter_does_not_lose_could_strong_form():
    parsed=values('could')
    strong=next(v for v in parsed if v['text']=='kʊd' and 'strong form' in v['display_labels'])
    assert strong['representative'] and 'US' in strong['accent_families']
    assert not any(v['representative'] for v in parsed if v['text']=='kʉd')


def test_actual_audio_parent_accents_reach_nested_ipa():
    for word,ipa in [('rocket','ˈɹɑ.kət'),('mathematics','mæθ(.ə)ˈmæt.əks')]:
        target=next(v for v in values(word) if v['text']==ipa)
        assert 'US' in target['accent_labels']


def test_actual_mixed_transcription_keeps_play_ga_and_no_regional_default():
    parsed=values('play')
    ga=next(v for v in parsed if v['text']=='pleɪ̯' and v['notation']=='phonemic')
    assert ga['representative'] and 'US' in ga['accent_families']
    assert not next(v for v in parsed if v['text']=='ple(j)')['representative']


def test_actual_compound_scope_retains_geography_and_or_condition():
    compound=next(v for v in values('embarrass') if v['text']=='ɪmˈbɛɹəs')
    assert 'GA' in compound['accent_labels']
    assert any('with the Mmmm' in expr for expr in compound['scope_expressions'])
    alternatives=[v for v in values('on') if any(' or ' in expr for expr in v['scope_expressions'])]
    assert alternatives and all(v['label_binding_pending'] and not v['representative'] for v in alternatives)


def test_actual_parallel_ipa_keeps_line_scope_without_copying_variant_qualifier():
    second=next(v for v in values('extract') if v['text']=='ɛkˈstɹækt')
    assert 'verb' in second['qualifier_labels']
    portrait=values('portrait')
    normal=next(v for v in portrait if v['text']=='ˈpɔɹtɹət')
    rare=next(v for v in portrait if v['text']=='ˈpɔɹtɹeɪt')
    assert 'GenAm' in rare['accent_labels'] and 'rare' in rare['qualifier_labels']
    assert 'rare' not in normal['qualifier_labels']


def test_fixture_lines_are_exactly_bound_to_retained_originals():
    folder=quality.rich.ROOT/'test-artifacts/netem-display-quality-20261009/evidence-full'
    if not (folder/'manifest.json').exists():
        pytest.skip('Local retained full-source archive not present')
    pages,_=quality.load_evidence(folder)
    for word,item in FIXTURE['words'].items():
        original=pages[word]
        assert original['revision']==item['revision']
        assert quality.rich.sha(original['text'].encode())==item['source_body_sha256']
        for n in item['retained_line_numbers']:
            assert item['text'].splitlines()[n-1]==original['text'].splitlines()[n-1]
