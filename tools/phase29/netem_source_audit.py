"""Fingerprint-bound automated source reviews; previews and isolated writes only.

Meaning, declared POS and source pronunciation are independent decisions. This
never creates or edits human-confirmed core meanings or legacy/user fields.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/phase29'))
import netem_rich_import as rich

from app.services.rich_dictionary_parser import (
    HAN,
    HEADING,
    LEGACY_POS_CODES,
    POS_LABELS,
    RECOVERY_POS,
    Node,
    Tree,
    parse_wikitext,
    recover_wikitext_candidates,
    render_definition_markup,
    valid_ipa,
    wikitext_language_map,
)

AUDIT_VERSION = 'netem-source-audit-v1'
REVIEW_KIND = 'automated_source_semantic_comparison'
SOURCE_POS = {**RECOVERY_POS, 'article': 'det', '冠詞': 'det', '冠词': 'det'}


def verified_cedict_conditions(directory):
    directory=directory.resolve()
    if not directory.is_relative_to(ROOT/'test-artifacts'):
        raise ValueError('licence evidence must stay under test-artifacts')
    manifest_path=directory/'manifest.json'
    manifest=json.loads(manifest_path.read_bytes())
    expected=['https://cc-cedict.org/wiki/',
              'https://creativecommons.org/licenses/by-sa/3.0/legalcode',
              'https://zh.wiktionary.org/w/api.php?action=query&format=json&formatversion=2&prop=revisions&revids=5583383&rvprop=ids%7Ccontent&rvslots=main']
    if [r['url'] for r in manifest['files']]!=expected:
        raise ValueError('unrecognized CC-CEDICT conditions')
    ledger={};bodies=[]
    for record in manifest['files']:
        path=(directory/record['file']).resolve()
        if not path.is_relative_to(directory) or rich.sha(path.read_bytes())!=record['sha256']:
            raise ValueError('licence fingerprint mismatch')
        bodies.append(path.read_bytes())
        ledger[path.relative_to(ROOT).as_posix()]={'sha256':record['sha256'],'bytes':path.stat().st_size}
    page=json.loads(bodies[2])['query']['pages'][0]
    revision=page['revisions'][0]
    licence_tree=Tree()
    licence_tree.feed(bodies[1].decode('utf-8'))
    licence_text=' '.join(licence_tree.root.text().split())
    if (page['title']!='Template:CC-CEDICT' or revision['revid']!=5583383
            or 'cc-by-sa-3.0' not in revision['slots']['main']['content']
            or 'a later version of this License' not in licence_text):
        raise ValueError('CC-CEDICT legacy licence path not established')
    checksum=rich.sha(manifest_path.read_bytes())
    ledger[manifest_path.relative_to(ROOT).as_posix()]={'sha256':checksum,'bytes':manifest_path.stat().st_size}
    return {'evidence_sha256':checksum,'legacy_license':'CC-BY-SA-3.0','adapter_license':'CC-BY-SA-4.0'},ledger


def with_cedict_attribution(item, originals, conditions):
    if (item.get('reason')!='license_marker_requires_review'
            or item.get('language')!='en' or item.get('source',{}).get('family')!='zhwiktionary'
            or item.get('quality_review',{}).get('meaning_result')!='accept'
            or re.search(r'\{\{|\}\}|-\{|<[^>]+>',item['text'])):
        return item
    original=next((o for o in originals if o['body_sha256']==item['source']['body_sha256']),None)
    if not original or '{{CC-CEDICT}}' not in original['text']:
        return item
    result=copy.deepcopy(item)
    result['source_before_licence_review']=copy.deepcopy(item['source'])
    source=result['source'];attribution=source['attribution']
    source['license_id']='CC-BY-SA-3.0 (CC-CEDICT original); CC-BY-SA-4.0 (adaptation)'
    attribution['creators']+='；CC-CEDICT 项目贡献者与编辑团队（通过该固定维基修订引用）'
    attribution['links']=[*attribution.get('links',[]),
        {'label':'CC-CEDICT 原料来源与贡献者','url':'https://cc-cedict.org/wiki/'},
        {'label':'CC-CEDICT 原料许可 3.0','url':'https://creativecommons.org/licenses/by-sa/3.0/'},
        {'label':'固定许可标记','url':'https://zh.wiktionary.org/w/index.php?oldid=5583383'}]
    attribution['modifications']+=' 本轮补齐 CC-CEDICT 原料署名和原 3.0 许可；原文不重新许可。仅本项目格式整理、选择及标注的改编依 3.0 第4(b)节后续版本路径按 BY-SA 4.0 提供。'
    result.update(status='source_verified',reason='',license_review=conditions)
    return result


def declared_pos(raw):
    abbreviations={'n.':'noun','v.':'verb','vt.':'verb','vi.':'verb','adj.':'adj','adv.':'adv',
                   'prep.':'prep','pron.':'pron','conj.':'conj','interj.':'interj'}
    if raw in abbreviations:
        return abbreviations[raw]
    inline_codes={'[副]':'adv','[介]':'prep','[名]':'noun','[动]':'verb','[動]':'verb',
                  '[形]':'adj','[代]':'pron','[连]':'conj','[連]':'conj'}
    if raw in inline_codes:
        return inline_codes[raw]
    direct = SOURCE_POS.get(raw, SOURCE_POS.get(raw.lower(), ''))
    marker = re.fullmatch(r'\{\{=(n|v|a|adj|adv)=\|(?:英|en)(?:\|[^{}]*)?\}\}', raw)
    if marker:
        return {'n': 'noun', 'v': 'verb', 'a': 'adj', 'adj': 'adj', 'adv': 'adv'}[marker[1]]
    simple=re.fullmatch(r'\{\{-(\w+)-\}\}',raw)
    if simple:
        return LEGACY_POS_CODES.get(simple[1],'')
    return direct


def enforce_source_language(item, originals):
    if item['source'].get('family')!='zhwiktionary':
        return item
    original=next((o for o in originals if o.get('body_sha256')==item['source'].get('body_sha256')),None)
    if original is None:
        raise ValueError('missing original for language-region check')
    line=int(item.get('sense_path') or item['locator'].rsplit(':',1)[1])
    language=wikitext_language_map(original['text']).get(line,'')
    if language=='en':
        return item
    result=copy.deepcopy(item)
    result.update(status='pending',reason='source_language_boundary_conflict',language=language or 'unknown')
    result['language_review_note']='固定原行处于其它语言或未明确语言区域，不以词头和中文碰巧一致认作英语释义。'
    return result


def recovered_reviews(records, snapshot_path, review_paths, fetched=False):
    """Bind the separately reviewed recovery formats to actual saved source lines."""
    snapshots=[snapshot_path] if isinstance(snapshot_path,Path) else snapshot_path
    paths=[*snapshots,*review_paths]
    if any(not p.resolve().is_relative_to(ROOT/'test-artifacts') for p in paths):
        raise ValueError('recovery evidence must stay under test-artifacts')
    candidates=[candidate for path in snapshots for candidate in json.loads(path.read_bytes())]
    decisions={}
    ledger={}
    for path in paths:
        raw=path.read_bytes()
        ledger[path.resolve().relative_to(ROOT).as_posix()]={'sha256':rich.sha(raw),'bytes':len(raw)}
    for path in review_paths:
        review=json.loads(path.read_bytes())
        if review.get('review_kind')!=REVIEW_KIND or review.get('core_confirmation') is not False:
            raise ValueError('invalid recovered source review')
        for d in review['decisions']:
            key=(d['entry_id'],d['locator'])
            if key in decisions: raise ValueError('duplicate recovered review')
            decisions[key]=d
    expected={(s['entry_id'],s['locator']) for s in candidates}
    if len(expected)!=len(candidates) or set(decisions)!=expected:
        raise ValueError('recovered review coverage mismatch')
    by_id={r['entry_id']:r for r in records}
    added={}
    for candidate in candidates:
        record=by_id.get(candidate['entry_id'])
        if not record or record['word']!=candidate['word']:
            raise ValueError('recovered entry identity drift')
        original=next((o for o in record['payload']['originals']
                       if o['body_sha256']==candidate['body_sha256']),None)
        line=int(candidate['sense_path'])
        if (original is None or original['text'].splitlines()[line-1]!=candidate['raw_text']
                or candidate['locator']!=f'zhwiktionary:{original["revision"]}:{line}'
                or candidate['source']!=original['source']):
            raise ValueError('recovered original source-line binding mismatch')
        values=recover_wikitext_candidates(original['text'])
        if candidate.get('normalization_version','') not in {'','regional-v1','annotations-v2'}:
            raise ValueError('unknown source normalization version')
        if fetched:
            values=parse_wikitext(original['text'])['senses']+values
            for value in values:
                value['text']=render_definition_markup(value['text'],
                    regional_conversion=candidate.get('normalization_version') in {'regional-v1','annotations-v2'},
                    extended_annotations=candidate.get('normalization_version')=='annotations-v2')[0]
        parsed={s['sense_path']:s for s in values}
        if candidate['sense_path'] in parsed:
            current=parsed[candidate['sense_path']]
            if any(current.get(key,'')!=candidate.get(key,'')
                   for key in ['text','pos_raw','pos_key','pos_locator','language']):
                raise ValueError('recovered text/POS normalization changed')
        result=audit_sense(candidate,decisions[(candidate['entry_id'],candidate['locator'])],
                           entry_id=candidate['entry_id'],word=candidate['word'],index=candidate['i'])
        result=enforce_source_language(result,record['payload']['originals'])
        if candidate['sense_path'] not in parsed and result['language']=='en':
            result.update(status='pending',reason='source_recovery_noise_boundary',pos_key='',pos_label='')
        if re.search(r'\((?:Source|來源|来源)\s*:',result['text'],re.IGNORECASE):
            result.update(status='pending',reason='third_party_source_requires_review')
        added.setdefault(candidate['entry_id'],[]).append(result)
    return added,ledger


def validate_fetched_sources(records, directory, originals_path):
    directory=directory.resolve()
    if not directory.is_relative_to(ROOT/'test-artifacts') or not originals_path.resolve().is_relative_to(ROOT/'test-artifacts'):
        raise ValueError('fetched originals must stay in test-artifacts')
    manifest_path=directory/'manifest.json'
    manifest=json.loads(manifest_path.read_bytes())
    if manifest.get('publisher')!='Chinese Wiktionary contributors' or manifest.get('license')!='CC-BY-SA-4.0':
        raise ValueError('unknown fetched source/conditions')
    verified={};ledger={}
    for item in manifest['responses']:
        path=(directory/item['file']).resolve()
        if not path.is_relative_to(directory) or rich.sha(path.read_bytes())!=item['sha256']:
            raise ValueError('fetched API fingerprint mismatch')
        ledger[path.relative_to(ROOT).as_posix()]={'sha256':item['sha256'],'bytes':path.stat().st_size}
        for page in json.loads(path.read_bytes()).get('query',{}).get('pages',[]):
            for revision in page.get('revisions',[]):
                text=revision.get('slots',{}).get('main',{}).get('content')
                if text is not None:
                    verified[str(revision['revid'])]=(page['title'],text,path.relative_to(ROOT).as_posix(),item['sha256'])
    originals=json.loads(originals_path.read_bytes())
    identities={str(r['entry_id']):r['word'] for r in records}
    for entry_id,rows in originals.items():
        if entry_id not in identities: raise ValueError('fetched entry identity mismatch')
        for original in rows:
            value=verified.get(str(original['revision']))
            if (not value or value[0]!=identities[entry_id] or original['headword']!=value[0]
                    or original['text']!=value[1] or original['file']!=value[2] or original['file_sha256']!=value[3]
                    or rich.sha(original['text'].encode())!=original['body_sha256']
                    or original['source']['original_file_sha256']!=value[3]
                    or original['source']['body_sha256']!=original['body_sha256']
                    or str(original['source']['revision'])!=str(original['revision'])
                    or original['source']['file']!=original['file']
                    or original['source']['family']!='zhwiktionary'):
                raise ValueError('fetched source page/revision/body binding mismatch')
    for path in [manifest_path,originals_path]:
        ledger[path.resolve().relative_to(ROOT).as_posix()]={'sha256':rich.sha(path.read_bytes()),'bytes':path.stat().st_size}
    return originals,ledger


@lru_cache(maxsize=128)
def html_gloss_structure(raw):
    tree = Tree()
    tree.feed(raw)
    nodes = list(tree.root.descendants())

    def direct_text(node):
        return ''.join(child if isinstance(child, str) else
                       ('' if child.tag in {'div', 'ol', 'ul'} else direct_text(child))
                       for child in node.children).strip()

    result = {}
    for node in nodes:
        if node.tag != 'li':
            continue
        glosses = []
        for child in [node, *node.descendants()]:
            if isinstance(child, Node) and child.tag == 'li':
                value = direct_text(child)
                if re.search(r'[A-Za-z]', value):
                    glosses.append(value)
        result[node.path] = glosses
    return result


def audit_sense(sense, decision, *, entry_id, word, index):
    expected = {'entry_id': entry_id, 'word': word, 'i': index,
                'locator': sense['locator'], 'text': sense['text'],
                'body_sha256': sense['source']['body_sha256']}
    if any(decision.get(k) != value for k, value in expected.items()):
        raise ValueError('source review binding mismatch')
    if (decision.get('meaning') not in {'accept', 'reject', 'pending'}
            or decision.get('pos') not in {'accept', 'unknown'}
            or decision.get('scope') not in {'sense', 'word_translation'}
            or not str(decision.get('note', '')).strip()):
        raise ValueError('invalid automated review decision')
    if decision['meaning'] == 'accept' and (sense.get('language') != 'en' or not HAN.search(sense['text'])):
        raise ValueError('only located English-source Chinese meanings can be accepted')
    result = copy.deepcopy(sense)
    result['previous_extraction'] = {'status': sense.get('status'), 'reason': sense.get('reason')}
    result['source_pos_key'] = sense.get('pos_key', '')
    result['source_pos_label'] = sense.get('pos_label', '')
    result['semantic_scope'] = decision['scope']
    if sense['source'].get('family') == 'wikdict':
        result['source_glosses'] = html_gloss_structure(sense.get('raw_text', '')).get(sense.get('sense_path', ''), [])
        if len(result['source_glosses']) > 1:
            result['semantic_scope'] = 'word_translation'
    if sense.get('reason') == 'shared_translation_multiple_glosses' and not sense.get('has_bound_sense'):
        result['semantic_scope'] = 'word_translation'
    result['quality_review'] = {'kind': REVIEW_KIND, 'audit_version': AUDIT_VERSION,
                               'meaning_result': decision['meaning'], 'pos_result': decision['pos'],
                               'note': decision['note'], 'core_confirmation': False}
    if result['semantic_scope'] == 'word_translation' and len(result.get('source_glosses', [])) > 1:
        result['quality_review']['alignment_note'] = (
            '同一中文节点共用多个英语义项；仅保留核验的整词译文，不声明它对应列表中的每个英文义项。')
    source_pos = declared_pos(sense.get('pos_raw', ''))
    if decision['pos'] == 'accept' and source_pos:
        result.update(pos_key=source_pos, pos_label=POS_LABELS[source_pos])
        if sense.get('pos_raw', '').lower() == 'article' or sense.get('pos_raw') in {'冠詞', '冠词'}:
            result['pos_label'] = '冠词'
    else:
        result.update(pos_key='', pos_label='')
    if decision['meaning'] == 'accept':
        result.update(status='source_verified', reason='')
    elif decision['meaning'] == 'reject':
        result.update(status='rejected', reason='source_semantic_conflict')
    else:
        result.update(status='pending', reason='source_audit_unresolved')
    # Semantic agreement cannot clear unrelated licence or unresolved markup holds.
    if sense.get('reason') == 'license_marker_requires_review':
        result.update(status='pending', reason='license_marker_requires_review')
    elif decision['meaning'] == 'accept' and re.search(r'\{\{|\}\}|-\{|<[^>]+>', result['text']):
        result.update(status='pending', reason='unexpanded_template')
    return result


def audit_pronunciation(value):
    result = copy.deepcopy(value)
    result['previous_extraction'] = {'status': value.get('status'), 'reason': value.get('reason')}
    eligible = (value.get('language') == 'en' and value.get('source', {}).get('family') in {'wikdict', 'zhwiktionary', 'enwiktionary'}
                and value.get('source', {}).get('body_sha256')
                and value.get('source', {}).get('original_file_sha256') and value.get('locator')
                and valid_ipa(value.get('text', ''))
                and value.get('reason', '') in {'', 'unlabelled_variant_requires_review'})
    if eligible:
        result.update(status='source_verified', reason='', accent_status='unspecified')
        result['quality_review'] = {
            'kind': 'automated_source_format_and_binding_check', 'audit_version': AUDIT_VERSION,
            'note': '来自固定英语词典的原音标节点；通过字符和词头绑定核验，保留全部来源变体，不推断英美口音或标准读法。',
            'core_confirmation': False}
    return result


def english_context(text, line):
    lines = text.splitlines()
    if not isinstance(line, int) or not 1 <= line <= len(lines):
        raise ValueError('invalid fixed source line')
    stack = []
    language = ''
    for number, raw in enumerate(lines[:line], 1):
        heading = HEADING.fullmatch(raw.strip())
        if heading:
            level, title = len(heading[1]), heading[2].strip()
            stack = [item for item in stack if item[0] < level]
            stack.append((level, title, number))
            if level == 2:
                language = title
    if language != 'English':
        raise ValueError('citation is outside the English source section')
    for _level, title, number in reversed(stack):
        if title.lower() in SOURCE_POS:
            return SOURCE_POS[title.lower()], title, number
    return '', '', 0


def retained_supplements(connection, review_path):
    from app.services.public_lexicon_gap import verify_candidate

    expected = '5fcc8dfa504ab1d008ce9f015ab2848a6c65f75cf64474eb8d56188cb42ae146'
    candidate = ROOT / 'test-artifacts/netem-gap-20261005/candidate'
    rows, _sources = verify_candidate(candidate, expected)
    review_bytes = review_path.read_bytes()
    review = json.loads(review_bytes)
    if (review.get('review_kind') != 'automated_existing_source_translation_comparison'
            or review.get('core_confirmation') is not False or review.get('source_package_sha256') != expected):
        raise ValueError('invalid supplementary automated source review')
    decisions = {d['entry_id']: d for d in review['decisions']}
    if len(decisions) != len(review['decisions']) or set(decisions) != {r['entry_id'] for r in rows}:
        raise ValueError('supplement review coverage must exactly match the retained package')
    fp = json.loads((candidate / 'fingerprints.json').read_bytes())
    files = {str((candidate / name).relative_to(ROOT).as_posix()): {'sha256': checksum,
             'bytes': (candidate / name).stat().st_size} for name, checksum in fp['files'].items()}
    files[review_path.resolve().relative_to(ROOT).as_posix()] = {'sha256': rich.sha(review_bytes), 'bytes': len(review_bytes)}
    pages_path = candidate / 'snapshots/pages.json'
    pages = json.loads(pages_path.read_bytes())
    page_file_sha = rich.sha(pages_path.read_bytes())
    result = {}
    for row in rows:
        decision = decisions[row['entry_id']]
        if (decision.get('word') != row['word'] or decision.get('body_sha256') != row['page_text_sha256']
                or decision.get('adapter_text') != row['new_meanings'][0] or decision.get('meaning') != 'accept'
                or not decision.get('note') or not decision.get('values')):
            raise ValueError('supplementary review binding mismatch')
        entry = connection.execute('select * from lexicon_entry where id=?', (row['entry_id'],)).fetchone()
        if entry is None or entry['word'] != row['word'] or json.loads(entry['source_meanings']) != row['new_meanings']:
            raise ValueError('retained supplemental meaning changed')
        evidence = connection.execute('select ev.* from entry_source_evidence ev join source_artifact a '
            'on a.id=ev.source_artifact_id where ev.lexicon_entry_id=? and ev.field_kind=? '
            'and ev.selected_for_default=1 and a.name=?', (row['entry_id'], 'meaning', row['source_file'])).fetchall()
        if len(evidence) != 1 or evidence[0]['raw_text'] != row['new_meanings'][0] or evidence[0]['source_revision'] != row['oldid']:
            raise ValueError('supplemental adopted evidence binding drift')
        artifact = connection.execute('select * from source_artifact where id=?', (evidence[0]['source_artifact_id'],)).fetchone()
        if artifact['file_sha256'] != rich.sha((candidate / row['source_file']).read_bytes()):
            raise ValueError('supplement source adapter hash mismatch')
        source = dict(rich.provenance(artifact), family='enwiktionary', revision=row['oldid'],
                      file=pages_path.relative_to(ROOT).as_posix(), original_file_sha256=page_file_sha,
                      body_sha256=row['page_text_sha256'], source_page_title=row['source_title'])
        source['extraction_modifications'] = (
            '本次自动复核既有英文词典的中文选择/翻译及逐义原行对应；保留既有词形关系、作者和许可。'
            '义项按明确的原引用行映射，不按分号机械切分；不确认人工核心释义。')
        page = pages[row['source_title']]
        citations = {c['line']: c for c in row['citations']}
        values = []
        for choice in decision['values']:
            if (not choice.get('text') or choice['text'] not in row['new_meanings'][0]
                    or not choice.get('lines') or any(n not in citations for n in choice['lines'])):
                raise ValueError('supplementary selected value is not bound to retained text/citations')
            context = [english_context(page['text'], n) for n in choice['lines']]
            pos = context[0][0] if all(c[0] == context[0][0] for c in context) else ''
            values.append({'text': choice['text'], 'language': 'en', 'status': 'source_verified', 'reason': '',
                'meaning_kind': 'derived', 'semantic_scope': 'sense' if len(choice['lines']) == 1 else 'word_translation',
                'pos_key': pos, 'pos_label': POS_LABELS.get(pos, '') if pos else '',
                'pos_raw': context[0][1] if pos else '', 'pos_locator': str(context[0][2]) if pos else '',
                'locator': f'enwiktionary:{row["oldid"]}:L' + ','.join(map(str, choice['lines'])),
                'raw_text': '\n'.join(f'L{n}: {citations[n]["raw_text"]}' for n in choice['lines']),
                'source_citations': [citations[n] for n in choice['lines']],
                'adapter_raw_text': row['new_meanings'][0], 'source_evidence_id': evidence[0]['id'],
                'form_relation': row['form_relation'], 'source': source,
                'quality_review': {'kind': review['review_kind'], 'audit_version': AUDIT_VERSION,
                                   'note': decision['note'], 'core_confirmation': False}})
        originals = [{'text': page['text'], 'file': source['file'], 'file_sha256': page_file_sha,
                      'body_sha256': row['page_text_sha256'], 'revision': row['oldid'],
                      'headword': row['source_title'], 'source': source}]
        for relation in row['form_relation']:
            form = pages[relation['title']]
            originals.append({'text': form['text'], 'file': source['file'], 'file_sha256': page_file_sha,
                'body_sha256': relation['page_text_sha256'], 'revision': str(relation['oldid']),
                'headword': relation['title'], 'source': dict(source, body_sha256=relation['page_text_sha256'],
                                                            revision=str(relation['oldid']))})
        pronunciation_page = next((p for p in originals if p['headword'] == row['word']), None)
        pronunciations = []
        if pronunciation_page:
            for p in parse_wikitext(pronunciation_page['text'])['pronunciations']:
                p.update(source=pronunciation_page['source'],
                         locator=f'enwiktionary:{pronunciation_page["revision"]}:L{p["locator"]}')
                pronunciations.append(audit_pronunciation(p))
        result[row['entry_id']] = {'senses': values, 'originals': originals, 'pronunciations': pronunciations}
    return result, files


def load_reviews(paths, records, corrections_path=None):
    decisions, ledger = {}, {}
    for path in paths:
        path = path.resolve()
        if not path.is_relative_to(ROOT / 'test-artifacts'):
            raise ValueError('audit decisions must stay under test-artifacts')
        data = path.read_bytes()
        review = json.loads(data)
        if review.get('review_kind') != REVIEW_KIND or review.get('core_confirmation') is not False:
            raise ValueError('review must be automated, with no human/core confirmation')
        ledger[path.relative_to(ROOT).as_posix()] = {'sha256': rich.sha(data), 'bytes': len(data)}
        for decision in review['decisions']:
            key = (decision['entry_id'], decision['i'])
            if key in decisions:
                raise ValueError('duplicate source review decision')
            decisions[key] = decision
    expected = {(r['entry_id'], i) for r in records for i, _ in enumerate(r['payload']['senses'])}
    if set(decisions) != expected:
        raise ValueError('review coverage must exactly match all retained candidates')
    if corrections_path:
        corrections_path = corrections_path.resolve()
        if not corrections_path.is_relative_to(ROOT / 'test-artifacts'):
            raise ValueError('corrections must stay under test-artifacts')
        data = corrections_path.read_bytes()
        correction = json.loads(data)
        if correction.get('review_kind') != REVIEW_KIND or correction.get('core_confirmation') is not False:
            raise ValueError('correction is not an automated source review')
        seen = set()
        for item in correction['decisions']:
            key = (item['entry_id'], item['i'])
            if key in seen or key not in decisions or item.get('previous_decision_sha256') != rich.digest(decisions[key]):
                raise ValueError('stale or duplicate review correction')
            seen.add(key)
            decisions[key] = item
        ledger[corrections_path.relative_to(ROOT).as_posix()] = {'sha256': rich.sha(data), 'bytes': len(data)}
    return decisions, ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preview'])
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--review', type=Path, action='append', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--supplement-review', type=Path)
    parser.add_argument('--corrections', type=Path)
    parser.add_argument('--recovery-snapshot', type=Path)
    parser.add_argument('--recovery-review', type=Path, action='append')
    parser.add_argument('--fetched-dir', type=Path)
    parser.add_argument('--fetched-originals', type=Path)
    parser.add_argument('--fetched-snapshot', type=Path, action='append')
    parser.add_argument('--fetched-review', type=Path, action='append')
    parser.add_argument('--normalized-snapshot', type=Path, action='append')
    parser.add_argument('--normalized-review', type=Path, action='append')
    parser.add_argument('--cedict-conditions',type=Path)
    args = parser.parse_args()
    print(json.dumps(preview(args.database, args.inventory, args.review, args.out, args.limit, args.supplement_review, args.corrections,
                             args.recovery_snapshot, args.recovery_review, args.fetched_dir,
                             args.fetched_originals, args.fetched_snapshot, args.fetched_review,
                             args.normalized_snapshot,args.normalized_review,args.cedict_conditions),
                     ensure_ascii=False, indent=2))


def preview(database, inventory, review_paths, output, limit=None, supplement_review=None, corrections_path=None,
            recovery_snapshot=None,recovery_review_paths=None, fetched_dir=None,
            fetched_originals=None,fetched_snapshot=None,fetched_review_paths=None,
            normalized_snapshots=None,normalized_review_paths=None,cedict_conditions=None):
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'test-artifacts') or output.exists():
        raise ValueError('fresh preview output under test-artifacts required')
    records = json.loads(inventory.read_bytes())
    decisions, ledger = load_reviews(review_paths, records, corrections_path)
    recovered={}
    if recovery_snapshot or recovery_review_paths:
        if not recovery_snapshot or not recovery_review_paths:
            raise ValueError('recovery snapshot and review both required')
        recovered,recovery_files=recovered_reviews(records,recovery_snapshot,recovery_review_paths)
        ledger.update(recovery_files)
    fetched_values={}; extra_originals={}; binding_records=records
    fetched_options=[fetched_dir,fetched_originals,fetched_snapshot,fetched_review_paths]
    if any(fetched_options):
        if not all(fetched_options):
            raise ValueError('fetched originals, snapshot and review all required')
        extra_originals,files=validate_fetched_sources(records,fetched_dir,fetched_originals)
        ledger.update(files)
        # Keep the immutable production inventory for baseline checks. Only the
        # review binding view includes the new, fingerprint-validated originals.
        binding_records=copy.deepcopy(records)
        for record in binding_records:
            record['payload']['originals'].extend(extra_originals.get(str(record['entry_id']),[]))
        fetched_values,files=recovered_reviews(binding_records,fetched_snapshot,fetched_review_paths,fetched=True)
        ledger.update(files)
    normalized_values={}
    if normalized_snapshots or normalized_review_paths:
        if not normalized_snapshots or not normalized_review_paths:
            raise ValueError('normalized snapshot and review both required')
        normalized_values,files=recovered_reviews(binding_records,normalized_snapshots,normalized_review_paths,fetched=True)
        ledger.update(files)
    # Re-validate retained originals against pinned source fingerprints on each run.
    _wik, _zh, original_files = rich.local_sources()
    ledger.update(original_files)
    ledger[inventory.resolve().relative_to(ROOT).as_posix()] = {
        'sha256': rich.sha(inventory.read_bytes()), 'bytes': inventory.stat().st_size}
    plan_records = []
    conditions=None
    if cedict_conditions:
        conditions,files=verified_cedict_conditions(cedict_conditions)
        ledger.update(files)
    with rich.read_only(database) as c:
        lexicons = c.execute("select id from lexicon where name='NETEM' and source_type='netem'").fetchall()
        if len(lexicons) != 1:
            raise ValueError('unique NETEM required')
        lexicon_id = lexicons[0][0]
        entries = c.execute('select * from lexicon_entry where lexicon_id=? order by sequence,id', (lexicon_id,)).fetchall()
        if {e['id'] for e in entries} != {r['entry_id'] for r in records}:
            raise ValueError('NETEM inventory identity drift')
        by_id = {e['id']: e for e in entries}
        supplements = {}
        if supplement_review:
            supplements, supplement_files = retained_supplements(c, supplement_review)
            ledger.update(supplement_files)
        for record in records:
            entry = by_id[record['entry_id']]
            existing = c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?', (entry['id'],)).fetchone()
            if (entry['word'] != record['word'] or existing is None
                    or rich.digest(record['payload']) != record['payload_sha256']
                    or existing['payload_sha256'] != record['payload_sha256']
                    or rich.digest(json.loads(existing['payload'])) != record['payload_sha256']):
                raise ValueError('source extraction inventory drift')
            payload = copy.deepcopy(record['payload'])
            payload['senses'] = [audit_sense(sense, decisions[(entry['id'], i)], entry_id=entry['id'],
                                            word=entry['word'], index=i) for i, sense in enumerate(payload['senses'])]
            payload['senses']=[enforce_source_language(s,payload['originals']) for s in payload['senses']]
            payload['senses'].extend(recovered.get(entry['id'],[]))
            payload['senses'].extend(fetched_values.get(entry['id'],[]))
            payload['senses'].extend(normalized_values.get(entry['id'],[]))
            payload['pronunciations'] = [audit_pronunciation(p) for p in payload['pronunciations']]
            payload['pronunciations']=[enforce_source_language(p,payload['originals']) for p in payload['pronunciations']]
            for original in extra_originals.get(str(entry['id']),[]):
                payload['originals'].append(original)
                for pronunciation in parse_wikitext(original['text'])['pronunciations']:
                    pronunciation.update(source=original['source'],
                        locator=f'zhwiktionary:{original["revision"]}:{pronunciation["locator"]}')
                    payload['pronunciations'].append(enforce_source_language(
                        audit_pronunciation(pronunciation),[original]))
            if entry['id'] in supplements:
                # Existing common-value selections get display precedence after
                # this run's source recheck, without losing original candidates.
                payload['senses'] = supplements[entry['id']]['senses'] + payload['senses']
                for key in ['originals', 'pronunciations']:
                    payload[key].extend(supplements[entry['id']][key])
            payload['audit'] = {'version': AUDIT_VERSION, 'review_kind': REVIEW_KIND,
                                'input_payload_sha256': record['payload_sha256'], 'core_confirmation': False}
            if conditions:
                payload['senses']=[with_cedict_attribution(s,payload['originals'],conditions) for s in payload['senses']]
            shown = [s for s in payload['senses'] if s['status'] == 'source_verified']
            payload['audit']['input_failures']=list(payload.get('failures',[]))
            payload['failures']=[failure for failure in payload.get('failures',[])
                if not (failure=='pinned_wikitext_not_preserved_for_revision' and str(entry['id']) in extra_originals)
                and not (failure=='no_structurally_usable_chinese_sense' and shown)]
            baseline = rich.original_baseline(entry)
            changes = {'new_pos': sorted({s['pos_key'] for s in shown if s['pos_key']}),
                       'additional_originals':len(extra_originals.get(str(entry['id']),[])),
                       'recovered_format_candidates':len(recovered.get(entry['id'],[])),
                       'fetched_source_candidates':len(fetched_values.get(entry['id'],[])),
                       'recovered_sense_count': len(shown), 'multiple_senses': len(shown) > 1,
                       'display_pos': sorted({s['pos_key'] for s in shown if s['pos_key']}),
                       'display_sense_count': len(shown),
                       'display_ungrouped_sense_count': sum(not s['pos_key'] for s in shown),
                       'rejected_sense_count': sum(s['status'] == 'rejected' for s in payload['senses']),
                       'pending_sense_count': sum(s['status'] == 'pending' for s in payload['senses']),
                       'pronunciation_records': len(payload['pronunciations']),
                       'usable_pronunciations': sum(p['status'] == 'source_verified' for p in payload['pronunciations']),
                       'quarantined': sorted({s.get('reason', '') for s in payload['senses'] + payload['pronunciations']
                                              if s['status'] in {'pending', 'rejected'}}),
                       'old_meanings': json.loads(entry['source_meanings']), 'legacy_fields_preserved': True}
            plan_records.append({'entry_id': entry['id'], 'word': entry['word'], 'sequence': entry['sequence'],
                                 'baseline': baseline, 'baseline_sha256': rich.digest(baseline),
                                 'baseline_extraction_sha256': existing['payload_sha256'],
                                 'payload': payload, 'payload_sha256': rich.digest(payload), 'changes': changes})
    if limit is not None:
        if limit < len(rich.REQUIRED):
            raise ValueError('pilot must cover required representative words')
        priority = {*rich.REQUIRED, 'August', 'Bible', 'Christian', 'Christmas', 'December', 'Catholic',
                    'a', 'about', 'against', 'compass', 'pool', 'poor', 'zone',
                    'aboard','accompany','absolute','aeroplane','ability','actual',
                    'certify','snobbish','mountain'}
        plan_records.sort(key=lambda r: (r['word'] not in priority, r['sequence'], r['entry_id']))
        plan_records = plan_records[:limit]
    stats = rich.coverage(plan_records)
    stats.update(audit_version=AUDIT_VERSION,
                 without_usable_chinese_sense=sum(r['changes']['display_sense_count'] == 0 for r in plan_records),
                 without_verified_pos=sum(not r['changes']['new_pos'] for r in plan_records),
                 words_with_verified_ungrouped_meanings=sum(r['changes']['display_ungrouped_sense_count'] > 0 for r in plan_records),
                 excluded_fragments=sum(r['changes']['rejected_sense_count'] for r in plan_records),
                 unresolved_fragments=sum(r['changes']['pending_sense_count'] for r in plan_records),
                 reviewed_candidates=sum(len(r['payload']['senses']) for r in plan_records))
    plan = {'parser_version': rich.PARSER_VERSION, 'audit_version': AUDIT_VERSION, 'lexicon_id': lexicon_id,
            'source_files': ledger, 'records': plan_records, 'coverage': stats,
            'scope': 'automated source review; no human/core confirmation; legacy and user fields preserved'}
    output.mkdir(parents=True)
    (output / 'plan.json').write_text(rich.canonical(plan) + '\n', encoding='utf-8')
    (output / 'coverage.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'per-word.jsonl').open('w', encoding='utf-8') as f:
        for record in plan_records:
            f.write(rich.canonical({'entry_id': record['entry_id'], 'word': record['word'], **record['changes']}) + '\n')
    (output / 'representatives.json').write_text(json.dumps([r for r in plan_records if r['word'] in {
        *rich.REQUIRED, 'August', 'Bible', 'Christmas', 'December', 'Catholic', 'compass', 'a', 'about',
        'aboard','accompany','absolute','aeroplane','ability','actual','pop','rebellion'}],
        ensure_ascii=False, indent=2), encoding='utf-8')
    gaps=[]
    for record in plan_records:
        if record['changes']['display_sense_count']:
            continue
        values=record['payload']['senses']
        gaps.append({'entry_id':record['entry_id'],'word':record['word'],
                     'original_count':len(record['payload']['originals']),
                     'candidate_count':len(values),
                     'category':('no_parsed_chinese_candidate' if not values else
                                 'all_candidates_rejected' if all(s['status']=='rejected' for s in values)
                                 else 'unresolved_source_candidates'),
                     'reasons':record['changes']['quarantined'],
                     'candidates':[{'text':s['text'],'status':s['status'],'reason':s.get('reason',''),
                                    'locator':s['locator'],'note':s.get('quality_review',{}).get('note','')}
                                   for s in values]})
    (output/'remaining-gaps.json').write_text(json.dumps(gaps,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'plan_sha256': rich.sha((output / 'plan.json').read_bytes()), **stats}


if __name__ == '__main__':
    main()
