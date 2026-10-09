import copy
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/phase29'))
import netem_web_supplement as web


def page(text,title='certify'):
    return {'title':title,'oldid':'42','text':text,'body_sha256':web.rich.sha(text.encode()),
            'file':'test-artifacts/fixed.json','file_sha256':'f'*64,'timestamp':'2026-10-08T00:00:00Z',
            'family':'enwiktionary'}


def choice(**changes):
    return {'family':'enwiktionary','title':'certify','oldid':'42','text':'證明',
            'meaning_kind':'source','pos_key':'verb','definition_line':3,'translation_line':6,
            'citation_lines':[3,6],'usage_labels':[],'note':'原英义 attest 与明示中文證明对应。',
            'form_relation':[],**changes}


TEXT='==English==\n===Verb===\n# To attest to a fact as true.\n====Translations====\n{{trans-top|to attest}}\n*: Mandarin: {{t+|cmn|證明|tr=zhengming}}\n{{trans-bottom}}\n==French==\n===Verb===\n# unrelated'


def test_direct_translation_keeps_source_line_pos_and_no_human_confirmation():
    original=page(TEXT)
    value=web.bind_choice('certify',choice(),{('enwiktionary','certify'):original})
    assert value['text']=='證明' and value['pos_key']=='verb'
    assert value['status']=='source_verified' and value['meaning_kind']=='source'
    assert value['quality_review']['core_confirmation'] is False
    assert value['raw_text'].startswith('L3: # To attest')
    assert value['source']['body_sha256']==original['body_sha256']


def test_alt_and_linked_chinese_fields_render_without_inventing_text():
    text=TEXT.replace('證明|tr=zhengming','[[證]][[明]]|alt=證明事實')
    value=web.bind_choice('certify',choice(text='證明事實'),{('enwiktionary','certify'):page(text)})
    assert value['text']=='證明事實'
    with pytest.raises(ValueError,match='Chinese'):
        web.bind_choice('certify',choice(text='證明其它'),{('enwiktionary','certify'):page(text)})


def test_derived_translation_is_explicit_and_needs_an_actual_definition():
    value=web.bind_choice('certify',choice(text='证明事实属实',meaning_kind='derived',translation_line=None,
        citation_lines=[3]),{('enwiktionary','certify'):page(TEXT)})
    assert value['meaning_kind']=='derived' and value['text']=='证明事实属实'
    with pytest.raises(ValueError,match='definition'):
        web.bind_choice('certify',choice(text='例句不是定义',meaning_kind='derived',definition_line=5,
            translation_line=None,citation_lines=[5]),{('enwiktionary','certify'):page(TEXT)})


def test_source_language_pos_revision_and_quote_drift_fail_closed():
    pages={('enwiktionary','certify'):page(TEXT)}
    for delta in [{'definition_line':10,'citation_lines':[10,6]}, {'pos_key':'noun'}, {'oldid':'43'}]:
        with pytest.raises(ValueError):
            web.bind_choice('certify',choice(**delta),pages)
    changed=page(TEXT.replace('# To attest','#: To attest'))
    with pytest.raises(ValueError,match='definition'):
        web.bind_choice('certify',choice(),{('enwiktionary','certify'):changed})


def test_spelling_target_requires_actual_english_relation_line():
    variant=page('==English==\n===Adjective===\n# {{standard spelling of|en|skeptical|from=Commonwealth}}.','sceptical')
    target=page('==English==\n===Adjective===\n# Inclined to doubt.','skeptical')
    pages={('enwiktionary','sceptical'):variant,('enwiktionary','skeptical'):target}
    c=choice(title='skeptical',text='持怀疑态度的',pos_key='adj',meaning_kind='derived',
             translation_line=None,citation_lines=[3],form_relation=[{'source_title':'sceptical','line':3,'target_title':'skeptical'}])
    assert web.bind_choice('sceptical',c,pages)['form_relation'][0]['target_title']=='skeptical'
    c=copy.deepcopy(c);c['form_relation']=[]
    with pytest.raises(ValueError,match='relation'):
        web.bind_choice('sceptical',c,pages)


def test_examples_and_usage_note_lists_are_not_definition_evidence():
    for changed in [TEXT.replace('# To attest to a fact as true.','# {{ux|en|He certifies a fact.}}'),
                    TEXT.replace('===Verb===','===Verb===\n====Usage notes====')]:
        with pytest.raises(ValueError,match='definition'):
            web.bind_choice('certify',choice(),{('enwiktionary','certify'):page(changed)})


def test_qualifiers_come_from_actual_definition_not_review_label_guesses():
    changed=TEXT.replace('# To attest','# {{lb|en|proscribed|legal}} To attest')
    c=choice(usage_labels=['archaic'])
    value=web.bind_choice('certify',c,{('enwiktionary','certify'):page(changed)})
    assert value['display_usage_labels']==['不推荐的用法','法律']
    assert '古旧' not in value['display_usage_labels']


def test_inline_later_qualifiers_do_not_apply_to_the_first_definition_clause():
    text=TEXT.replace('# To attest to a fact as true.',
        '# {{sid|en|state}}{{lb|en|legal}} A sovereign state; {{lb|en|loosely|proscribed}} a country.')
    value=web.bind_choice('certify',choice(text='主权国家',meaning_kind='derived',translation_line=None,
        citation_lines=[3]),{('enwiktionary','certify'):page(text)})
    assert value['display_usage_labels']==['法律']


def test_also_figuratively_keeps_literal_and_additional_figurative_scope():
    text=TEXT.replace('# To attest to a fact as true.','# {{lb|en|also|figuratively}} An act of bringing something.')
    value=web.bind_choice('certify',choice(meaning_kind='derived',translation_line=None,citation_lines=[3]),
                          {('enwiktionary','certify'):page(text)})
    assert value['display_usage_labels']==['亦可用于比喻']
