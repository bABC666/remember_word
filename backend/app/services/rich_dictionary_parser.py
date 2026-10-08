"""Conservative local parsing. Extracted source text is never a core-meaning verdict."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

PARSER_VERSION = 'netem-rich-v1'
HAN = re.compile(r'[\u3400-\u9fff]')
POS = {
    'noun': 'noun', 'verb': 'verb', 'adjective': 'adj', 'adverb': 'adv',
    'pronoun': 'pron', 'preposition': 'prep', 'determiner': 'det',
    'conjunction': 'conj', 'interjection': 'interj', 'numeral': 'num',
    '名詞': 'noun', '名词': 'noun', '專有名詞': 'noun', '专有名词': 'noun',
    '動詞': 'verb', '动词': 'verb', '形容詞': 'adj', '形容词': 'adj',
    '副詞': 'adv', '副词': 'adv', '代詞': 'pron', '代词': 'pron',
    '介詞': 'prep', '介词': 'prep', '連詞': 'conj', '连词': 'conj',
    '限定詞': 'det', '限定词': 'det', '數詞': 'num', '数词': 'num',
    '感嘆詞': 'interj', '感叹词': 'interj',
}
POS_LABELS = {'noun': '名词', 'verb': '动词', 'adj': '形容词', 'adv': '副词',
              'pron': '代词', 'prep': '介词', 'det': '限定词', 'conj': '连词',
              'num': '数词', 'interj': '感叹词', '': '词性待核'}
RECOVERY_POS = {**POS, '介系詞':'prep', '介系词':'prep'}
LEGACY_POS_CODES = {'n':'noun','v':'verb','a':'adj','adj':'adj','adv':'adv',
                    **{name:key for name,key in POS.items() if name.isascii()}}
ENGLISH = {'英語', '英语', '英文', 'English', '{{en}}', '{{-en-}}'}
HEADING = re.compile(r'^(={2,6})\s*(.*?)\s*\1\s*$')
ANCILLARY = re.compile(r'近[義义]|同[義义]|反[義义]|[衍派]生|用法|使用|[參参]考|'
                       r'[相有][關关]|延伸|[異异]序|翻[譯译]|[詞词]源|其他|替代|另[見见]|'
                       r'synonym|antonym|derived|related|usage|reference|translation|'
                       r'etymology|alternative|see also', re.IGNORECASE)


def clean_links(text: str) -> str:
    text = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]+)\]\]', r'\1', text)
    return re.sub(r"'{2,5}", '', unescape(text)).strip()


def render_definition_markup(text: str, *, regional_conversion=False, extended_annotations=False) -> tuple[str, list[str]]:
    """Render only explicit, simple usage annotations; retain unknown markup."""
    labels=[]
    if extended_annotations:
        text=re.sub(r'\{\{m\|en\|([^{}|]+)\}\}',r'\1',text)
        text=re.sub(r'\{\{m\|en\|\|([^{}|]+)\}\}',r'\1',text)
        text=re.sub(r'\{\{gl\|([^{}|]+)\}\}',lambda m:'（'+m[1]+'）',text)
        def simple_label(match):
            labels.append(match[1])
            return '（'+match[1]+'）'
        text=re.sub(r'\{\{(及物|不及物|transitive|intransitive|英式)\}\}',simple_label,text)
        text=re.sub(r'\{\{(不及物)\|(喻)\}\}',lambda m:'（'+m[1]+'、'+m[2]+'）',text)
        text=re.sub(r'\{\{zh-l\|([^{}|]+)\}\}',r'\1',text)

    def label(match):
        values=[v.strip() for v in match[1].split('|') if v.strip()]
        if any('=' in value or '{' in value for value in values): return match[0]
        labels.extend(values)
        return '（'+'、'.join(values)+'）'

    text=re.sub(r'\{\{(?:lb|label)\|en\|([^{}]+)\}\}',label,text)
    text=re.sub(r'\{\{(?:zhushi|defdate)\|([^{}|]+)\}\}',label,text)
    text=re.sub(r'\{\{senseid\|en\|[^{}|]+\}\}','',text)
    if regional_conversion:
        variant=re.fullmatch(r'-\{\s*(?:[\[［])?(正體|简体)(?:[\]］])?\s*[:：]\s*([^{}]+)\}-',text)
        if variant:
            labels.append(variant[1])
            text='（'+variant[1]+'）'+variant[2].strip()
    return clean_links(text).strip(),labels


def valid_ipa(value: str) -> bool:
    # No language inference, truncation, XML annotations or selection among variants.
    return (1 <= len(value) <= 150 and not value.startswith('-')
            and not re.search(r'[;<>|{}=\n\r]', value) and not HAN.search(value)
            and bool(re.search(r'[a-zɑ-ʯ]', value))
            and bool(re.fullmatch(r"[a-zæçðøŋœθβχɑ-ʯˈˌːˑ.˞ˠˤ\u0300-\u036fʰʷʲʳˡⁿɫ\sˀˁ]+", value)))


class Node:
    def __init__(self, tag='', attrs=None, parent=None, number=0):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children: list[Node | str] = []
        self.path = f'{parent.path}/{tag}[{number}]' if parent else ''

    def text(self, exclude_translations=False):
        return ''.join(c if isinstance(c, str) else
                       ('' if exclude_translations and c.tag == 'div' else
                        c.text(exclude_translations)) for c in self.children).strip()

    def descendants(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()

    def handle_starttag(self, tag, attrs):
        number = 1 + sum(isinstance(c, Node) and c.tag == tag for c in self.current.children)
        child = Node(tag, attrs, self.current, number)
        self.current.children.append(child)
        if tag not in {'br', 'hr', 'img', 'meta', 'input', 'link'}:
            self.current = child

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_wikdict(word: str, body: str) -> dict:
    tree = Tree()
    tree.feed(body)
    nodes = list(tree.root.descendants())
    grammars = [n for n in nodes if n.attrs.get('class') == 'grammar']
    raw_pos = grammars[0].text() if len(grammars) == 1 else ''
    pos = POS.get(raw_pos, '')
    groups: dict[str, dict] = {}
    for node in nodes:
        if node.tag != 'div' or not HAN.search(node.text()) or any(
                isinstance(c, Node) for c in node.children):
            continue
        ancestor = node
        excluded = False
        while ancestor:
            classes = ancestor.attrs.get('class', '')
            if (ancestor.tag in {'blockquote', 'table', 'script', 'style'}
                    or re.search('example|etymology|related|usage|synonym|antonym', classes, re.IGNORECASE)
                    or ancestor.attrs.get('lang', 'en') not in {'en', 'zh', 'zh-Hans', 'zh-Hant'}):
                excluded = True
                break
            ancestor = ancestor.parent
        if excluded:
            continue
        # Translation lists inside a sense share that sense, never become fake new senses.
        ancestor = node.parent
        sense = None
        while ancestor and ancestor.parent:
            if ancestor.tag == 'li' and re.search('[A-Za-z]', ancestor.text(True)):
                sense = ancestor
                break
            ancestor = ancestor.parent
        path = sense.path if sense else node.path
        record = groups.setdefault(path, {
            'text': '', 'translations': [], 'raw_text': body, 'sense_path': path,
            'node_paths': [], 'pos_key': pos, 'pos_label': POS_LABELS[pos],
            'pos_raw': raw_pos, 'pos_locator': grammars[0].path if grammars else '',
            'language': 'en', 'language_basis': 'pinned English-Chinese package',
            'status': 'extracted', 'reason': '',
            'source_gloss': sense.text(True) if sense else node.parent.text(True),
            'has_bound_sense': sense is not None,
        })
        value = node.text()
        if value not in record['translations']:
            record['translations'].append(value)
        record['node_paths'].append(node.path)
    for record in groups.values():
        record['text'] = '、'.join(record['translations'])
        reason = ''
        if not pos:
            reason = 'missing_or_multiple_pos'
        elif not record['has_bound_sense'] and len([n for n in nodes if n.tag == 'li']) > 1:
            reason = 'shared_translation_multiple_glosses'
        if word == 'above' and '酸' in record['translations']:
            reason = 'upstream_translation_conflict_over_sour'
        if word == 'set':
            reason = 'upstream_set_translation_quality_requires_review'
        if word == 'April' and pos == 'pron':
            reason = 'upstream_proper_name_pronoun_requires_review'
        if reason:
            record.update(status='pending', reason=reason)
    pronunciations = []
    for n in nodes:
        if n.tag == 'font' and n.attrs.get('color') == 'gray':
            value = n.text().strip()
            if valid_ipa(value):
                pronunciations.append({'text': value, 'locator': n.path, 'raw_text': body,
                                       'language': 'en', 'status': 'pending',
                                       'reason': 'unlabelled_variant_requires_review'})
    return {'senses': list(groups.values()), 'pronunciations': pronunciations}


def parse_wikitext(text: str) -> dict:
    senses, pronunciations = [], []
    stack: list[tuple[int, str]] = []
    pos, pos_raw, pos_line = '', '', 0
    blocked = None
    in_english = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        heading = HEADING.fullmatch(line)
        if heading:
            level, title = len(heading[1]), clean_links(heading[2])
            stack = [(depth, name) for depth, name in stack if depth < level]
            stack.append((level, title))
            if level == 2:
                in_english = title in ENGLISH
                pos, pos_raw, pos_line, blocked = '', '', 0, None
            else:
                if blocked is not None and (level <= abs(blocked) or blocked < 0):
                    blocked = None
                if blocked is None and ANCILLARY.search(title):
                    # Etymology headings may contain POS subsections: block its body only.
                    blocked = level if not re.search('[詞词]源|etymology', title, re.IGNORECASE) else -level
                if blocked is None and title in POS:
                    pos, pos_raw, pos_line = POS[title], title, number
                elif level <= 3:
                    pos, pos_raw, pos_line = '', '', 0
            continue
        if not in_english or blocked is not None or not line:
            continue
        old_pos = re.fullmatch(r'\{\{=(n|v|a|adj|adv)=\|(?:英|en)(?:\|[^{}]*)?\}\}', line)
        if old_pos:
            pos = {'n': 'noun', 'v': 'verb', 'a': 'adj', 'adj': 'adj', 'adv': 'adv'}[old_pos[1]]
            pos_raw, pos_line = line, number
            continue
        for template in re.findall(r'\{\{IPA\|en\|([^{}]+)\}\}', line):
            for value in template.split('|'):
                if value.startswith('/') and value.endswith('/'):
                    pronunciations.append({'text': value[1:-1], 'locator': str(number),
                                           'raw_text': raw, 'language': 'en',
                                           'status': 'extracted' if valid_ipa(value[1:-1]) else 'pending',
                                           'reason': '' if valid_ipa(value[1:-1]) else 'ipa_format_requires_review',
                                           'accent_raw': line})
        if line.startswith(('#:', '#*', '#;', '*', ':', ';', '[[Category:', '[[分类:', '<', '|')):
            continue
        is_definition = bool(re.match(r'^#+\s*[^:#*;]', line))
        is_bare = bool(pos and not line.startswith(('{', '[')) and HAN.search(line))
        is_link = bool(pos and re.fullmatch(r'\[\[[^\]]+\]\]', line))
        if not (is_definition or is_bare or is_link):
            continue
        value = clean_links(re.sub(r'^#+\s*', '', line))
        reason = ('unexpanded_template' if '{{' in value or '-{' in value or '<' in value
                  else 'missing_pos_heading' if not pos
                  else 'bare_line_requires_review' if is_bare and not is_definition else '')
        if not HAN.search(value):
            continue
        senses.append({'text': value, 'raw_text': raw, 'sense_path': str(number),
                       'depth': len(line) - len(line.lstrip('#')),
                       'pos_key': pos, 'pos_label': POS_LABELS[pos], 'pos_raw': pos_raw,
                       'pos_locator': str(pos_line) if pos_line else '',
                       'language': 'en', 'language_basis': stack[0][1],
                       'heading_path': ' > '.join(name for _, name in stack),
                       'status': 'pending' if reason else 'extracted', 'reason': reason})
    if 'CC-CEDICT' in text:
        for item in [*senses, *pronunciations]:
            item.update(status='pending', reason='license_marker_requires_review')
    return {'senses': senses, 'pronunciations': pronunciations}


def old_language_marker(line: str) -> str | None:
    codes = re.findall(r'\{\{-([a-z]{2,3})-\}\}', line)
    codes = [code for code in codes if code not in {'adj', 'adv'}]
    return codes[-1] if codes else None


def wikitext_language_map(text: str) -> dict[int, str]:
    """Track explicit language headers and the older language-switch templates."""
    language = ''
    result = {}
    for number, raw in enumerate(text.splitlines(), 1):
        heading = HEADING.fullmatch(raw.strip())
        if heading and len(heading[1]) == 2:
            title = clean_links(heading[2])
            language = 'en' if title in ENGLISH else title
        marker = old_language_marker(raw)
        if marker:
            language = 'en' if marker in {'en', 'eng'} else marker
        result[number] = language
    return result


def recover_wikitext_candidates(text: str) -> list[dict]:
    """Locate previously skipped formats for a separate, mandatory source review.

    These are candidates only. Plain definitions do not need a POS, categories
    are not used to infer one, and punctuation never creates synthetic senses.
    """
    found = []
    already_parsed={s['sense_path'] for s in parse_wikitext(text)['senses']}
    english = False
    blocked = None
    template_depth = 0
    pos, pos_raw, pos_line = '', '', 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        language_marker = old_language_marker(line)
        if language_marker:
            english = language_marker in {'en', 'eng'}
            blocked, template_depth = None, 0
            pos, pos_raw, pos_line = '', '', 0
            continue
        heading = HEADING.fullmatch(line)
        if heading:
            level, title = len(heading[1]), clean_links(heading[2])
            if level == 2:
                english = title in ENGLISH
                blocked, template_depth = None, 0
                pos, pos_raw, pos_line = '', '', 0
            else:
                if blocked is not None and (level <= abs(blocked) or blocked < 0):
                    blocked = None
                if blocked is None and ANCILLARY.search(title):
                    blocked = level if not re.search('[詞词]源|etymology', title, re.IGNORECASE) else -level
                if blocked is None and title in RECOVERY_POS:
                    pos, pos_raw, pos_line = RECOVERY_POS[title], title, number
                elif level <= 3:
                    pos, pos_raw, pos_line = '', '', 0
            continue
        if not english or blocked is not None or not line:
            continue
        if str(number) in already_parsed:
            continue
        marker = re.fullmatch(r'\{\{(?:=(n|v|a|adj|adv)=\|(?:英|en)(?:\|[^{}]*)?|-(\w+)-)\}\}(?:\{\{[nva]-en\|[^{}]*\}\})?', line)
        if marker:
            if (marker[1] or marker[2]) not in LEGACY_POS_CODES:
                continue
            pos = LEGACY_POS_CODES[marker[1] or marker[2]]
            pos_raw, pos_line = line[:line.index('}}')+2], number
            continue
        translation=re.fullmatch(r'\*\s*\{\{(zh|zh-hans|zh-hant)\}\}\s*[:：]\s*(.+)',line)
        inline_ipa=re.fullmatch(r':/[^/]+/\s*\[([副介名动動形代连連])\]\s*(.+)',line)
        depth_before = template_depth
        template_depth = max(0, template_depth + line.count('{{') - line.count('}}'))
        if depth_before or (not translation and ('{{' in line or '}}' in line)):
            continue
        content=re.sub(r'^\*#?\s*','',line)
        if re.search(r'^(?:例[句证證]|音[頻频標标]|.*[發发][音]|一格单数|[請请]參考|[請请]参考)', content):
            continue
        bullet_link = bool(pos and re.fullmatch(r'\*\s*\[\[[^\]]+\]\]', line))
        bullet=bool(re.match(r'^\*#?[^*:]',line) and not re.match(r'^[A-Za-z"“]',clean_links(content)))
        linked_bare=bool(line.startswith('[[') and not re.match(r'\[\[(?:Category|分类|分類):',line))
        numbered_hash=bool(re.match(r'^#+\s*[^:#*;]',line))
        if not (bullet or linked_bare or translation or inline_ipa or numbered_hash) and line.startswith(('#', '*', ':', ';', '[', '{', '<', '|', '}', '=')):
            continue
        if not HAN.search(line):
            continue
        value = clean_links(content if bullet else line)
        if numbered_hash:
            value=clean_links(re.sub(r'^#+\s*','',line))
        inline = re.match(r'^(n|v|vt|vi|adj|adv|prep|pron|conj|interj)\.\s+(.+)$', value)
        current_pos, current_raw, current_line = pos, pos_raw, pos_line
        if translation:
            value=translation[2]
            if value.startswith('-{') and value.endswith('}-'):
                value=value[2:-2]
            value=clean_links(value)
            current_pos,current_raw,current_line='', '',0
        if inline_ipa:
            current_pos={'副':'adv','介':'prep','名':'noun','动':'verb','動':'verb',
                         '形':'adj','代':'pron','连':'conj','連':'conj'}[inline_ipa[1]]
            current_raw,current_line,value='['+inline_ipa[1]+']',number,clean_links(inline_ipa[2])
        if inline:
            current_pos = {'n': 'noun', 'v': 'verb', 'vt': 'verb', 'vi': 'verb', 'adj': 'adj',
                           'adv': 'adv', 'prep': 'prep', 'pron': 'pron', 'conj': 'conj', 'interj': 'interj'}[inline[1]]
            current_raw, current_line, value = inline[1] + '.', number, inline[2]
        numbered = bool(re.match(r'^\d+[.．、]\s*', value))
        if numbered:
            value = re.sub(r'^\d+[.．、]\s*', '', value)
        found.append({'text': value, 'raw_text': raw, 'sense_path': str(number),
                      'language': 'en', 'language_basis': 'explicit English language section',
                      'pos_key': current_pos, 'pos_label': POS_LABELS.get(current_pos, ''),
                      'pos_raw': current_raw, 'pos_locator': str(current_line) if current_line else '',
                      'status': 'pending', 'reason': 'recovered_format_requires_semantic_review',
                      'semantic_scope': 'sense' if bullet_link or numbered else 'word_translation',
                      'recovery_format': 'bullet_link' if bullet_link else 'numbered_plain' if numbered else 'bare'})
    if 'CC-CEDICT' in text:
        for item in found:
            item['reason'] = 'license_marker_requires_review'
    return found
