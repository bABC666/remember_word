from app.services.rich_dictionary_parser import parse_wikdict, parse_wikitext


def test_html_senses_follow_nodes_and_keep_semicolon_in_one_sense():
    result = parse_wikdict('play', '<div><font class="grammar">verb</font></div>'
                           '<ol><li>sport<div>玩；游戏</div></li>'
                           '<li>music<div>演奏</div></li></ol>')
    assert [(s['pos_key'], s['text']) for s in result['senses']] == [
        ('verb', '玩；游戏'), ('verb', '演奏')]
    assert result['senses'][0]['sense_path'] != result['senses'][1]['sense_path']
    assert all(s['language'] == 'en' for s in result['senses'])


def test_nested_translations_share_sense_and_ambiguous_siblings_are_pending():
    result = parse_wikdict('play', '<font class="grammar">verb</font>'
                           '<ol><li>music<ol><li><div>打</div></li>'
                           '<li><div>拉</div></li></ol></li></ol>')
    assert len(result['senses']) == 1
    assert result['senses'][0]['text'] == '打、拉'
    result = parse_wikdict('run', '<font class="grammar">verb</font>'
                           '<ol><li>move</li><li>spread</li></ol><div>跑</div>')
    assert result['senses'][0]['reason'] == 'shared_translation_multiple_glosses'


def test_upstream_defects_and_pronunciation_noise_are_not_displayable():
    above = parse_wikdict('above', '<font class="grammar">preposition</font>'
                         '<ol><li>over, on top of<div>酸</div></li></ol>')
    assert above['senses'][0]['status'] == 'pending'
    april = parse_wikdict('April', '<font class="grammar">pronoun</font>month<div>四月</div>')
    assert april['senses'][0]['status'] == 'pending'
    result = parse_wikdict('x', '<font color="gray">;</font><font color="gray">-əl</font>'
                          '<font class="grammar">noun</font>something<div>物</div>')
    assert result['pronunciations'] == []


def test_wikitext_english_pos_boundaries_templates_examples_and_hierarchy():
    text = ('==法語==\n===名詞===\n# 外語\n==英語==\n'
            '===詞源===\n# 词源噪声\n===形容詞===\n'
            '# [[大量]]的，[[丰盛]]的\n#: 例句\n# {{unknown|噪声}}\n'
            '====相關詞====\n# 相关词噪声\n=====名詞=====\n# 仍是噪声\n'
            '===名詞===\n# 名词义\n## 子义\n===發音===\n'
            '* {{IPA|en|/əˈbʌn.dn̩t/}}\n==德語==\n* {{IPA|de|/x/}}')
    result = parse_wikitext(text)
    assert [s['text'] for s in result['senses'] if s['status'] == 'extracted'] == [
        '大量的，丰盛的', '名词义', '子义']
    assert len(result['pronunciations']) == 1
    assert any(s['reason'] == 'unexpanded_template' for s in result['senses'])
    assert all(s['language'] == 'en' for s in result['senses'])


def test_old_style_pos_templates_bare_link_and_no_pos_not_inferred():
    result = parse_wikitext('==[[英语]]==\n{{=n=|英|play}}\n[[剧]]\n'
                            '{{=v=|英|play}}\n[[遊玩]]\n[[演奏]]')
    assert [(s['pos_key'], s['text']) for s in result['senses']] == [
        ('noun', '剧'), ('verb', '遊玩'), ('verb', '演奏')]
    result = parse_wikitext('==英語==\n# 释义\n* 杂项')
    assert result['senses'][0]['pos_key'] == ''
    assert result['senses'][0]['status'] == 'pending'


def test_html_examples_other_languages_and_related_lists_are_excluded():
    result = parse_wikdict('x', '<font class="grammar">noun</font><div>义项</div>'
                           '<blockquote><div>例句</div></blockquote>'
                           '<div class="example"><div>例句二</div></div>'
                           '<div lang="fr"><div>法语</div></div>'
                           '<div class="related"><div>相关词</div></div>')
    assert [s['text'] for s in result['senses']] == ['义项']


def test_common_english_ipa_and_explicit_old_adjective_marker_are_preserved():
    result = parse_wikitext('==英语==\n* {{IPA|en|/kæt/|/ðə/|/θɪŋ/}}\n'
                            '{{=a=|英|cold}}\n#[[冷]]')
    assert [p['text'] for p in result['pronunciations']] == ['kæt', 'ðə', 'θɪŋ']
    assert result['senses'][0]['pos_key'] == 'adj'
