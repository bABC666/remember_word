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
ENGLISH = {'英語', '英语', '英文', 'English', '{{en}}', '{{-en-}}'}
HEADING = re.compile(r'^(={2,6})\s*(.*?)\s*\1\s*$')
ANCILLARY = re.compile(r'近[義义]|同[義义]|反[義义]|[衍派]生|用法|使用|[參参]考|'
                       r'[相有][關关]|延伸|[異异]序|翻[譯译]|[詞词]源|其他|替代|另[見见]|'
                       r'synonym|antonym|derived|related|usage|reference|translation|'
                       r'etymology|alternative|see also', re.IGNORECASE)


def clean_links(text: str) -> str:
    text = re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]+)\]\]', r'\1', text)
    return re.sub(r"'{2,5}", '', unescape(text)).strip()


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
