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
    POS,
    POS_LABELS,
    Node,
    Tree,
    parse_wikitext,
    valid_ipa,
)

AUDIT_VERSION = 'netem-source-audit-v1'
REVIEW_KIND = 'automated_source_semantic_comparison'
SOURCE_POS = {**POS, 'article': 'det', '冠詞': 'det', '冠词': 'det'}


def declared_pos(raw):
    direct = SOURCE_POS.get(raw, SOURCE_POS.get(raw.lower(), ''))
    marker = re.fullmatch(r'\{\{=(n|v|a|adj|adv)=\|(?:英|en)(?:\|[^{}]*)?\}\}', raw)
    if marker:
        return {'n': 'noun', 'v': 'verb', 'a': 'adj', 'adj': 'adj', 'adv': 'adv'}[marker[1]]
    return direct


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
    args = parser.parse_args()
    print(json.dumps(preview(args.database, args.inventory, args.review, args.out, args.limit, args.supplement_review, args.corrections),
                     ensure_ascii=False, indent=2))


def preview(database, inventory, review_paths, output, limit=None, supplement_review=None, corrections_path=None):
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'test-artifacts') or output.exists():
        raise ValueError('fresh preview output under test-artifacts required')
    records = json.loads(inventory.read_bytes())
    decisions, ledger = load_reviews(review_paths, records, corrections_path)
    # Re-validate retained originals against pinned source fingerprints on each run.
    _wik, _zh, original_files = rich.local_sources()
    ledger.update(original_files)
    ledger[inventory.resolve().relative_to(ROOT).as_posix()] = {
        'sha256': rich.sha(inventory.read_bytes()), 'bytes': inventory.stat().st_size}
    plan_records = []
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
            payload['pronunciations'] = [audit_pronunciation(p) for p in payload['pronunciations']]
            if entry['id'] in supplements:
                # Existing common-value selections get display precedence after
                # this run's source recheck, without losing original candidates.
                payload['senses'] = supplements[entry['id']]['senses'] + payload['senses']
                for key in ['originals', 'pronunciations']:
                    payload[key].extend(supplements[entry['id']][key])
            payload['audit'] = {'version': AUDIT_VERSION, 'review_kind': REVIEW_KIND,
                                'input_payload_sha256': record['payload_sha256'], 'core_confirmation': False}
            shown = [s for s in payload['senses'] if s['status'] == 'source_verified']
            baseline = rich.original_baseline(entry)
            changes = {'new_pos': sorted({s['pos_key'] for s in shown if s['pos_key']}),
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
                    'a', 'about', 'against', 'compass', 'pool', 'poor', 'zone'}
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
        *rich.REQUIRED, 'August', 'Bible', 'Christmas', 'December', 'Catholic', 'compass', 'a', 'about'}],
        ensure_ascii=False, indent=2), encoding='utf-8')
    return {'plan_sha256': rich.sha((output / 'plan.json').read_bytes()), **stats}


if __name__ == '__main__':
    main()
