from app.services.rich_dictionary_parser import recover_wikitext_candidates


def test_plain_english_meanings_survive_missing_pos_without_fake_sense_splitting():
    text = '==英語==\n===發音===\n* {{IPA|en|/əˈbɔːd/}}\n在船上；上船\n[[分類:英语副词]]\n==法語==\n外语噪声'
    result = recover_wikitext_candidates(text)
    assert [s['text'] for s in result] == ['在船上；上船']
    assert result[0]['pos_key'] == ''
    assert result[0]['semantic_scope'] == 'word_translation'
    assert result[0]['status'] == 'pending'
    assert result[0]['sense_path'] == '4'


def test_star_link_definition_with_explicit_pos_does_not_include_examples_or_related_words():
    text = '==英语==\n===名词===\n*[[飛機]]\n* 例句：这是一架飞机。\n===相關詞===\n*[[航空]]\n例句：飞机起飞。'
    result = recover_wikitext_candidates(text)
    assert [(s['text'], s['pos_key']) for s in result] == [('飛機', 'noun')]


def test_multiline_templates_pronunciation_and_source_metadata_are_not_meanings():
    text = '==英语==\n* {{audio|en|sound.ogg|\n音頻（美式）}}\n(美式发音) : /x/\n=中文=\n一格单数=word\n例句：这是例句。\n== German ==\n中文噪声'
    assert recover_wikitext_candidates(text) == []


def test_inline_declared_pos_and_numbered_plain_definition_are_located_not_guessed():
    text = '==英语==\nn. 高度\n1.宇航员\n{{-v-}}\n灌溉'
    result = recover_wikitext_candidates(text)
    assert result[0]['text'] == '高度' and result[0]['pos_raw'] == 'n.' and result[0]['pos_key'] == 'noun'
    assert result[1]['text'] == '宇航员' and result[1]['sense_path'] == '3'
    assert result[2]['pos_key'] == 'verb'


def test_existing_numbered_and_bare_link_definitions_are_not_duplicated():
    assert recover_wikitext_candidates('==英语==\n===名词===\n# [[定义]]\n[[词条]]') == []


def test_old_language_templates_end_english_region_even_without_a_heading():
    text='==英语==\n菜单\n{{-mnc-}}\n赫舒{{-pt-}}\n菜单\n{{-en-}}\n英语译文'
    assert [s['text'] for s in recover_wikitext_candidates(text)] == ['菜单','英语译文']


def test_known_label_templates_preserve_qualifiers_and_unknown_templates_stay_visible_for_review():
    from app.services.rich_dictionary_parser import render_definition_markup
    text,labels=render_definition_markup('{{lb|en|及物|旧}} [[完成]] {{defdate|自15世紀}}')
    assert text=='（及物、旧） 完成 （自15世紀）'
    assert labels==['及物','旧','自15世紀']
    assert '{{unknown|义}}' in render_definition_markup('{{unknown|义}}')[0]
    assert '{{lb|fr|及物}}' in render_definition_markup('{{lb|fr|及物}}')[0]


def test_legacy_bullet_formats_and_combined_pos_marker_are_recovered_without_examples():
    text='==英语==\n{{-v-}}{{v-en|eject|ejects}}\n*[[逐出]]；[[排出]]\n===介系詞===\n*#[[針對]]\n===形容词===\n* 能，有能力的\n* 例句：他很能干。'
    rows=recover_wikitext_candidates(text)
    assert [(s['text'],s['pos_key']) for s in rows]==[
        ('逐出；排出','verb'),('針對','prep'),('能，有能力的','adj')]


def test_old_pos_names_and_linked_bare_meanings_keep_declared_pos_only():
    text='==英语==\n{{-pronoun-}}\n*[[每一個人]]\n==英语==\n[[习语]]、[[成语]]'
    rows=recover_wikitext_candidates(text)
    assert [(s['text'],s['pos_key']) for s in rows]==[('每一個人','pron'),('习语、成语','')]


def test_typed_chinese_translation_rows_and_inline_ipa_pos_are_candidates_not_examples():
    text='==英语==\n{{翻譯-頂}}\n* {{zh-hans}}：-{[[高声]]}-\n* {{fr}}：中文噪声\n{{翻譯-底}}\n:/ɪnˈsted/ [副]代替；反而\n:例句：\n:* He left. 他离开了。'
    rows=recover_wikitext_candidates(text)
    assert [(s['text'],s['pos_key']) for s in rows]==[('高声',''),('代替；反而','adv')]
    assert all(s['status']=='pending' for s in rows)


def test_old_english_marker_definitions_are_not_assumed_already_parsed():
    text='{{-en-}}\n===名詞===\n# 中文定义\n普通中文释义\n#: 中文例句'
    assert [s['text'] for s in recover_wikitext_candidates(text)]==['中文定义','普通中文释义']


def test_simple_regional_conversion_keeps_original_variant_and_domain():
    from app.services.rich_dictionary_parser import render_definition_markup
    text,labels=render_definition_markup('-{正體: 模糊[電子計算機]}-',regional_conversion=True)
    assert text=='（正體）模糊[電子計算機]' and labels==['正體']
    assert render_definition_markup('-{zh-hans:甲;zh-hant:乙}-',regional_conversion=True)[0]=='-{zh-hans:甲;zh-hant:乙}-'


def test_explicit_annotations_keep_qualifiers_and_english_link_text_without_guessing():
    from app.services.rich_dictionary_parser import render_definition_markup
    text,_=render_definition_markup('{{lb|en|不及物|+ {{m|en|of}}}} 由...构成 {{gl|整体}}',extended_annotations=True)
    assert text=='（不及物、+ of） 由...构成 （整体）'
    assert render_definition_markup('{{及物}} 管理',extended_annotations=True)[0]=='（及物） 管理'
    assert '{{法}}' in render_definition_markup('{{法}} 不明',extended_annotations=True)[0]
    assert '{{m|fr|词}}' in render_definition_markup('{{m|fr|词}}',extended_annotations=True)[0]
