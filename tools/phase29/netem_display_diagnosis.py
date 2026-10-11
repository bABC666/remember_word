"""Read-only rescan; conversion and shared ancestors are candidates, not verdicts."""
import argparse
import ctypes
import json
from collections import Counter, defaultdict
from pathlib import Path

import netem_rich_import as rich


def write_report(path,value):
    path=path.resolve()
    if not path.is_relative_to(rich.ROOT/'test-artifacts') or path.suffix!='.json':
        raise ValueError('JSON reports must stay outside protected data, under test-artifacts')
    body=json.dumps(value,ensure_ascii=False,indent=2)
    if path.exists():
        if rich.canonical(json.loads(path.read_bytes()))==rich.canonical(value):
            return
        raise FileExistsError('report/source already exists; use a fresh output filename')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(body,encoding='utf-8')


def simplified(text):
    buf = ctypes.create_unicode_buffer(len(text) * 3 + 16)
    n = ctypes.windll.kernel32.LCMapStringEx('zh-CN', 0x02000000, text, len(text), buf, len(buf), None, None, 0)
    if not n:
        raise ctypes.WinError()
    return buf[:n]


def eligible(p, key):
    audited = p.get('audit', {}).get('version') == 'netem-source-audit-v1'
    return [s for s in p.get(key, []) if s.get('language') == 'en' and (
        (s.get('status') == 'extracted' and (key != 'senses' or s.get('pos_key')))
        or (audited and s.get('status') == 'source_verified'))]


def records(database):
    with rich.read_only(database) as c:
        c.execute('pragma temp_store=memory')
        return [dict(row) | {'payload': json.loads(row['payload'])} for row in c.execute(
            'select e.id,e.lexicon_id,e.word,x.payload from lexicon_entry e join '
            'entry_dictionary_extraction x on x.lexicon_entry_id=e.id '
            'where e.lexicon_id=(select id from lexicon where name=?) order by e.sequence,e.id', ('NETEM',))]


def scan(database, out):
    wik, _, ledger = rich.local_sources()
    rows = records(database)
    report = {'entries': len(rows), 'source_files': ledger, 'script_duplicates': [],
              'same_gloss_candidates': [], 'ipa_over_two': [], 'source_binding_errors': [],
              'ipa_distribution': Counter(), 'sets_overlap': {}, 'core_confirmation': False}
    for row in rows:
        p = row['payload']
        for o in p['originals']:
            if o['source']['family'] == 'wikdict' and not any(
                s['body_sha256'] == o['body_sha256'] and s['text'] == o['text'] for s in wik[row['word']]):
                report['source_binding_errors'].append(row['word'])
        groups, glosses = defaultdict(list), defaultdict(list)
        for s in eligible(p, 'senses'):
            groups[(s.get('pos_key', ''), tuple(s.get('display_usage_labels', [])), simplified(s['text']))].append(s)
            if s['source']['family'] == 'wikdict':
                glosses[(s['locator'].split('/ol[')[0], s.get('pos_key', ''),
                         tuple(s.get('display_usage_labels', [])))].append(s)
        dup = [v for v in groups.values() if len({s['text'] for s in v}) > 1]
        alt = [v for v in glosses.values() if len({s['text'] for s in v}) > 1]
        if dup:
            report['script_duplicates'].append({'word': row['word'], 'groups': dup})
        if alt:
            report['same_gloss_candidates'].append({'word': row['word'], 'groups': alt})
        ipa = list(dict.fromkeys(s['text'] for s in eligible(p, 'pronunciations')))
        report['ipa_distribution'][len(ipa)] += 1
        if len(ipa) > 2:
            report['ipa_over_two'].append({'word': row['word'], 'ipa': ipa})
    sets = {k: {r['word'] for r in report[k]} for k in ['script_duplicates', 'same_gloss_candidates', 'ipa_over_two']}
    for a, av in sets.items():
        for b, bv in sets.items():
            if a < b:
                report['sets_overlap'][a + ' & ' + b] = len(av & bv)
    report['summary'] = {k: len(v) for k, v in sets.items()} | {
        'union': len(set.union(*sets.values())), 'source_binding_errors': len(report['source_binding_errors'])}
    write_report(out,report)
    return report['summary'] | {'entries': len(rows), 'overlap': report['sets_overlap']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(scan(args.database, args.out), ensure_ascii=False, indent=2))
