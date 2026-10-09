"""Read-only preview and explicit isolated update of existing NETEM entry IDs.

No source download, no lexical guessing, no concise confirmation. Production is
deliberately refused; the reviewed release is a separate operator action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.rich_dictionary_parser import PARSER_VERSION, parse_wikdict, parse_wikitext
from app.testing_guards import assert_not_real_data, is_protected_database, is_test_scratch_path

BASE = ROOT / 'test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research'
WIK_SHA = '62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830'
CACHE_SHA = '50c7cda85bdaa731afb0d25caca39c23a8248497c332a8a10014ce3b32978ff6'
PILOT_SHA = '09650bf2143440c5810b992a35a24ab8171094c6055aca111c02fe2a73a82a46'
REQUIRED = ['abundant', 'above', 'absent', 'run', 'set', 'play', 'April']
# Negative source-quality evidence remains a hold, never an adoption/approval.
KNOWN_SOURCE_HOLDS = {'shortage', 'temporary', 'thoughtful', 'tribute', 'tonight',
                      'ashore', 'set', 'complex', 'bit', 'honor', 'inhabit', 'pitch',
                      'progressive', 'build', 'collect', 'locate', 'outward', 'operation'}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value) -> str:
    return sha(canonical(value).encode())


def read_only(path: Path):
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def freeze_file(path: Path, expected: str, ledger: dict) -> bytes:
    data = path.read_bytes()
    if sha(data) != expected:
        raise ValueError(f'source fingerprint mismatch: {path}')
    ledger[path.relative_to(ROOT).as_posix()] = {'sha256': expected, 'bytes': len(data)}
    return data


def local_sources() -> tuple[dict, dict, dict]:
    ledger = {}
    cache_path = BASE / 'reports/zhwiktionary-full-coverage.json'
    cache = json.loads(freeze_file(cache_path, CACHE_SHA, ledger))
    wik = defaultdict(list)
    path = BASE / 'raw/wikdict-en-zh.zip'
    freeze_file(path, WIK_SHA, ledger)
    with zipfile.ZipFile(path) as z:
        index = z.read('wikdict-en-zh/stardict.idx')
        dictionary = z.read('wikdict-en-zh/stardict.dict')
        info = z.read('wikdict-en-zh/stardict.ifo').decode()
        if 'English-' not in info or 'by-sa/4.0' not in info:
            raise ValueError('pinned bilingual package/attribution declaration missing')
    cursor = number = 0
    while cursor < len(index):
        end = index.find(b'\0', cursor)
        if end < 0 or end + 9 > len(index):
            raise ValueError('malformed StarDict index')
        offset = int.from_bytes(index[end + 1:end + 5], 'big')
        size = int.from_bytes(index[end + 5:end + 9], 'big')
        if offset + size > len(dictionary):
            raise ValueError('StarDict record outside dictionary')
        word = index[cursor:end].decode()
        raw = dictionary[offset:offset + size]
        wik[word].append({'text': raw.decode(), 'file': path.relative_to(ROOT).as_posix(),
                          'file_sha256': WIK_SHA, 'body_sha256': sha(raw),
                          'revision': '2026-06-23', 'index': number,
                          'index_byte_offset': cursor, 'dict_byte_offset': offset,
                          'dict_byte_length': size, 'headword': word})
        cursor, number = end + 9, number + 1
    pages = {}
    # Lock old manifests against the committed evidence index when indexed.
    evidence_index = json.loads((ROOT / 'docs/NETEM-V2-LAUNCH-EVIDENCE-2026-10-05.json').read_bytes())['files']
    snapshots = [
        (ROOT / 'test-artifacts/netem-launch-decision-20261005/upstream', 'fetch-manifest.json'),
        (ROOT / 'test-artifacts/default-lexicon-gap-20261004', 'snapshot-manifest.json')]
    for folder, manifest_name in snapshots:
        manifest_path = folder / manifest_name
        key = manifest_path.relative_to(ROOT).as_posix()
        manifest_bytes = freeze_file(manifest_path, evidence_index.get(key, sha(manifest_path.read_bytes())), ledger)
        manifest = json.loads(manifest_bytes)
        items = manifest.get('responses') if manifest_name == 'snapshot-manifest.json' else [
            dict(meta, file=name) for name, meta in manifest.items() if name.startswith('original-')]
        for item in items:
            path = (folder / item['file']).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError('snapshot path escape')
            raw = freeze_file(path, item['sha256'], ledger)
            for page in json.loads(raw).get('query', {}).get('pages', []):
                for rev in page.get('revisions', []):
                    text = rev['slots']['main']['content']
                    fact = {'text': text, 'headword': page['title'], 'revision': str(rev['revid']),
                            'file': path.relative_to(ROOT).as_posix(), 'file_sha256': sha(raw),
                            'body_sha256': sha(text.encode())}
                    old = pages.get(str(rev['revid']))
                    if old and (old['text'], old['headword']) != (text, page['title']):
                        raise ValueError('conflicting preserved revision')
                    pages[str(rev['revid'])] = fact
    path = BASE / 'rehearsal/pilot/zh-pinned-wikitext-300.json'
    for oldid, text in json.loads(freeze_file(path, PILOT_SHA, ledger)).items():
        title = next((d['word'] for d in cache.values() if str(d.get('oldid')) == oldid), None)
        if title:
            pages.setdefault(oldid, {'text': text, 'headword': title, 'revision': oldid,
                             'file': path.relative_to(ROOT).as_posix(), 'file_sha256': PILOT_SHA,
                             'body_sha256': sha(text.encode())})
    return wik, {'cache': cache, 'pages': pages}, ledger


def original_baseline(row) -> dict:
    return {key: row[key] for key in ['id', 'lexicon_id', 'word', 'normalized_word', 'source_raw',
                                     'source_meanings', 'phonetic', 'part_of_speech', 'default_anchor']}


def provenance(artifact) -> dict:
    if sha(artifact['mapping_json'].encode()) != artifact['mapping_sha256']:
        raise ValueError('existing attribution/mapping fingerprint mismatch')
    fields = ['id', 'name', 'publisher', 'version', 'license_id', 'license_text_sha256',
              'mapping_json', 'mapping_sha256', 'file_sha256', 'obtained_at_utc']
    value = {f: artifact[f] for f in fields}
    value['attribution'] = json.loads(artifact['mapping_json']).get('attribution', {})
    value['extraction_modifications'] = (
        '本次保留大小写完全匹配的原始词头，按英语词性标题、HTML义项节点或原文行整理；'
        '不按分号切分，不推断词性，不选择音标变体，不确认核心释义；歧义保留待核。')
    return value


def preview(database: Path, output: Path, limit: int | None, review_path: Path | None = None) -> dict:
    # Source data is read only, including a real DB. Reports must go outside data/.
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'test-artifacts') or output.exists():
        raise ValueError('preview needs a fresh output directory under test-artifacts')
    wik, zh, ledger = local_sources()
    reviews = {}
    if review_path:
        review_bytes = review_path.read_bytes()
        review = json.loads(review_bytes)
        if review['review_kind'] != 'automated_source_comparison' or review['core_confirmation'] is not False:
            raise ValueError('source review must not claim human/core confirmation')
        reviews = {item['locator']: item for item in review['accepted']}
        if len(reviews) != len(review['accepted']):
            raise ValueError('duplicate source review locator')
        ledger[review_path.resolve().relative_to(ROOT).as_posix()] = {
            'sha256': sha(review_bytes), 'bytes': len(review_bytes)}
    with read_only(database) as c:
        lexicons = c.execute("select id from lexicon where name='NETEM' and source_type='netem'").fetchall()
        if len(lexicons) != 1:
            raise ValueError('one NETEM lexicon required, no fixed lexicon ID')
        lexicon_id = lexicons[0][0]
        entries = c.execute('select * from lexicon_entry where lexicon_id=? order by sequence,id',
                            (lexicon_id,)).fetchall()
        artifacts = {a['name']: a for a in c.execute('select * from source_artifact')}
        wiki_meta, zh_meta = provenance(artifacts['wikdict.csv']), provenance(artifacts['zhwiktionary.csv'])
        selected_revisions = {r['normalized_word']: r['source_revision'] for r in c.execute(
            "select normalized_word,source_revision from entry_source_evidence where "
            "source_artifact_id=? and selected_for_default=1", (zh_meta['id'],))}
        records = []
        for entry in entries:
            word = entry['word']
            sources = [(original, wiki_meta, 'wikdict') for original in wik.get(word, [])]
            cache = zh['cache'].get(entry['normalized_word'], {})
            oldid = selected_revisions.get(entry['normalized_word']) or str(cache.get('oldid', ''))
            page = zh['pages'].get(oldid)
            if page and page['headword'] == word:
                sources.append((page, zh_meta, 'zhwiktionary'))
            payload = {'senses': [], 'pronunciations': [], 'originals': [], 'failures': []}
            if not wik.get(word):
                payload['failures'].append('no_exact_wikdict_headword')
            if not page or page['headword'] != word:
                payload['failures'].append('pinned_wikitext_not_preserved_for_revision')
            for original, meta, family in sources:
                source = dict(meta, family=family, file=original['file'],
                              original_file_sha256=original['file_sha256'],
                              body_sha256=original['body_sha256'], revision=original['revision'])
                payload['originals'].append(dict(original, source=source))
                parsed = parse_wikdict(word, original['text']) if family == 'wikdict' else parse_wikitext(original['text'])
                for field in ['senses', 'pronunciations']:
                    for item in parsed[field]:
                        locator = (f"wikdict:idx:{original['index']}:offset:{original['dict_byte_offset']}:"
                                   f"{item.get('sense_path', item.get('locator'))}" if family == 'wikdict' else
                                   f"zhwiktionary:{oldid}:{item.get('sense_path', item.get('locator'))}")
                        payload[field].append(dict(item, source=source, locator=locator))
            usable = [s for s in payload['senses'] if s['status'] == 'extracted']
            for sense in payload['senses']:
                sense['structural_status'] = sense['status']
                if word in KNOWN_SOURCE_HOLDS:
                    sense.update(status='pending', reason='prior_source_quality_hold_requires_review')
                if sense['source']['family'] != 'wikdict' or sense['status'] != 'extracted':
                    continue
                checked = reviews.get(sense['locator'])
                if (checked and checked['word'] == word and checked['text'] == sense['text']
                        and checked['pos_key'] == sense['pos_key']
                        and checked['body_sha256'] == sense['source']['body_sha256']):
                    sense['quality_review'] = {'kind': 'automated_source_comparison',
                                               'note': checked['note'], 'core_confirmation': False}
                else:
                    sense.update(status='pending', reason='translation_quality_not_reviewed')
            if not usable:
                payload['failures'].append('no_structurally_usable_chinese_sense')
            reasons = sorted({s['reason'] for s in payload['senses'] + payload['pronunciations']
                              if s['status'] == 'pending'})
            baseline = original_baseline(entry)
            old_meanings = json.loads(entry['source_meanings'])
            changes = {'new_pos': sorted({s['pos_key'] for s in usable}),
                       'recovered_sense_count': len(usable),
                       'multiple_senses': len(usable) > 1,
                       'pronunciation_records': len(payload['pronunciations']),
                       'usable_pronunciations': sum(p['status'] == 'extracted' for p in payload['pronunciations']),
                       'quarantined': reasons, 'old_meanings': old_meanings,
                       'legacy_fields_preserved': True}
            displayed = [s for s in payload['senses'] if s['status'] == 'extracted']
            changes.update(display_pos=sorted({s['pos_key'] for s in displayed}),
                           display_sense_count=len(displayed))
            records.append({'entry_id': entry['id'], 'word': word, 'sequence': entry['sequence'],
                            'baseline': baseline, 'baseline_sha256': digest(baseline),
                            'payload': payload, 'payload_sha256': digest(payload), 'changes': changes})
        if limit is not None:
            wanted = set(REQUIRED)
            chosen = [r for r in records if r['word'] in wanted]
            # Explicitly include absent-source/template/multiple-POS cases before rank fill.
            predicates = [lambda r: not r['payload']['originals'],
                          lambda r: 'unexpanded_template' in r['changes']['quarantined'],
                          lambda r: len(r['changes']['new_pos']) > 1,
                          lambda r: 'no_structurally_usable_chinese_sense' in r['payload']['failures']]
            for predicate in predicates:
                chosen.extend(next(([r] for r in records if predicate(r) and r not in chosen), []))
            chosen.extend(r for r in records if r not in chosen)
            records = chosen[:limit]
    stats = coverage(records)
    plan = {'parser_version': PARSER_VERSION, 'lexicon_id': lexicon_id,
            'source_files': ledger, 'records': records, 'coverage': stats,
            'scope': 'source extraction, no core confirmation, no legacy/user-field update'}
    output.mkdir(parents=True)
    (output / 'plan.json').write_text(canonical(plan) + '\n', encoding='utf-8')
    (output / 'coverage.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (output / 'per-word.jsonl').open('w', encoding='utf-8') as f:
        for r in records:
            f.write(canonical({'entry_id': r['entry_id'], 'word': r['word'], **r['changes'],
                               'failures': r['payload']['failures']}) + '\n')
    (output / 'representatives.json').write_text(json.dumps(
        [r for r in records if r['word'] in REQUIRED], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'plan_sha256': sha((output / 'plan.json').read_bytes()), **stats}


def coverage(records: list[dict]) -> dict:
    return {'words': len(records),
            'with_structural_pos_and_meaning': sum(bool(r['changes']['new_pos']) for r in records),
            'with_multiple_pos': sum(len(r['changes']['new_pos']) > 1 for r in records),
            'with_multiple_source_senses': sum(r['changes']['multiple_senses'] for r in records),
            'with_displayable_unconfirmed_source_senses': sum(r['changes']['display_sense_count'] > 0 for r in records),
            'with_multiple_displayable_source_senses': sum(r['changes']['display_sense_count'] > 1 for r in records),
            'with_located_english_ipa': sum(r['changes']['usable_pronunciations'] > 0 for r in records),
            'with_raw_pronunciation_candidates': sum(r['changes']['pronunciation_records'] > 0 for r in records),
            'without_usable_chinese_sense': sum(not r['changes']['new_pos'] for r in records),
            'pending_reasons': dict(Counter(reason for r in records for reason in r['changes']['quarantined'])),
            'failures': dict(Counter(reason for r in records for reason in r['payload']['failures'])),
            'confirmed_core_meanings_added': 0, 'canonical_phonetic_fields_changed': 0}


def writable_isolated(database: Path):
    database = database.resolve()
    assert_not_real_data(database, action='update isolated rich import in')
    if is_protected_database(database) or not is_test_scratch_path(database):
        raise ValueError('isolated scratch database required; production release is not authorized')
    if not database.is_file():
        raise ValueError('explicit existing cloned database required')
    real = ROOT / 'data/vocab.db'
    if real.is_file() and database.samefile(real):
        raise ValueError('production hardlink refused')
    c = sqlite3.connect(database)
    c.row_factory = sqlite3.Row
    c.execute('pragma foreign_keys=on')
    return c


def table_fingerprints(c) -> dict:
    result = {}
    for row in c.execute("select name from sqlite_master where type='table' order by name"):
        name = row[0]
        if name in {'entry_dictionary_extraction', 'alembic_version', 'sqlite_sequence'}:
            continue
        # sqlite_master supplies the identifier; quote it rather than interpolate untrusted input.
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [list(r) for r in c.execute(f'select * from {quoted} order by rowid')]
        result[name] = {'rows': len(rows), 'sha256': digest(rows)}
    return result


def apply(database: Path, plan_path: Path, expected_sha: str, receipt_path: Path,
          *, _connect=None, _verify=None) -> dict:
    data = plan_path.read_bytes()
    if sha(data) != expected_sha:
        raise ValueError('reviewed plan checksum mismatch')
    plan = json.loads(data)
    if plan['parser_version'] != PARSER_VERSION:
        raise ValueError('parser version mismatch')
    for name, meta in plan['source_files'].items():
        path = (ROOT / name).resolve()
        if (not (path.is_relative_to(ROOT / 'test-artifacts') or
                 path == ROOT / 'tools/phase29/netem-rich-pilot-review.json')
                or sha(path.read_bytes()) != meta['sha256']):
            raise ValueError(f'original source changed: {name}')
    receipt_path = receipt_path.resolve()
    if not receipt_path.is_relative_to(ROOT / 'test-artifacts'):
        raise ValueError('receipt must stay in test-artifacts')
    prior_receipt = json.loads(receipt_path.read_bytes()) if receipt_path.exists() else None
    if prior_receipt and (Path(prior_receipt['database']).resolve() != database.resolve()
                          or prior_receipt['plan_sha256'] != expected_sha):
        raise ValueError('existing receipt belongs to another database/plan')
    with (_connect or writable_isolated)(database) as c:
        c.execute('begin immediate')
        if _verify:
            _verify(c)
        before = table_fingerprints(c)
        undo, changed = [], 0
        for record in plan['records']:
            row = c.execute('select * from lexicon_entry where id=?', (record['entry_id'],)).fetchone()
            if (row is None or row['lexicon_id'] != plan['lexicon_id'] or
                    digest(original_baseline(row)) != record['baseline_sha256'] or
                    digest(record['payload']) != record['payload_sha256']):
                raise ValueError(f'entry/source baseline drift: {record["word"]}')
            existing = c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?',
                                 (row['id'],)).fetchone()
            if existing and existing['payload_sha256'] == record['payload_sha256']:
                if digest(json.loads(existing['payload'])) != record['payload_sha256']:
                    raise ValueError('existing payload checksum corruption')
                continue
            if ('baseline_extraction_sha256' in record and
                    (existing is None or existing['payload_sha256'] != record['baseline_extraction_sha256']
                     or digest(json.loads(existing['payload'])) != record['baseline_extraction_sha256'])):
                raise ValueError('extraction baseline drift; later source update must be preserved')
            undo.append({'entry_id': row['id'], 'before': dict(existing) if existing else None,
                         'after_sha256': record['payload_sha256']})
            c.execute('insert into entry_dictionary_extraction '
                      '(lexicon_entry_id,parser_version,payload_sha256,payload) values (?,?,?,?) '
                      'on conflict(lexicon_entry_id) do update set parser_version=excluded.parser_version,'
                      'payload_sha256=excluded.payload_sha256,payload=excluded.payload',
                      (row['id'], PARSER_VERSION, record['payload_sha256'], canonical(record['payload'])))
            changed += 1
        after = table_fingerprints(c)
        if before != after or c.execute('pragma foreign_key_check').fetchall():
            raise ValueError('preservation/integrity check failed; transaction rolled back')
        if prior_receipt:
            if changed:
                raise ValueError('existing receipt has later drift; use rollback or a new receipt')
            return {'database': str(database.resolve()), 'plan_sha256': expected_sha,
                    'changed': 0, 'receipt_reused': True,
                    'integrity_check': c.execute('pragma integrity_check').fetchone()[0]}
        receipt = {'database': str(database.resolve()), 'plan_sha256': expected_sha,
                   'changed': changed, 'undo': undo, 'preserved_tables': before,
                   'integrity_check': c.execute('pragma integrity_check').fetchone()[0]}
        # Write the undo receipt before commit; an interrupted commit is safely detected by rollback.
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        with receipt_path.open('x', encoding='utf-8') as f:
            f.write(canonical(receipt) + '\n')
        c.commit()
    return {k: v for k, v in receipt.items() if k not in {'undo', 'preserved_tables'}}


def rollback(database: Path, receipt_path: Path, *, _connect=None, _verify=None) -> dict:
    receipt = json.loads(receipt_path.read_bytes())
    if Path(receipt['database']).resolve() != database.resolve():
        raise ValueError('rollback database identity mismatch')
    with (_connect or writable_isolated)(database) as c:
        c.execute('begin immediate')
        if _verify:
            _verify(c)
        before = table_fingerprints(c)
        changed = 0
        for item in receipt['undo']:
            row = c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?',
                            (item['entry_id'],)).fetchone()
            previous = item['before']
            if row is None and previous is None:
                continue
            if row and previous and dict(row) == previous:
                continue
            if (row is None or row['payload_sha256'] != item['after_sha256'] or
                    digest(json.loads(row['payload'])) != item['after_sha256']):
                raise ValueError('later extraction changed; optimistic rollback refused')
            if previous:
                c.execute('update entry_dictionary_extraction set parser_version=?,payload_sha256=?,payload=? '
                          'where lexicon_entry_id=?', (previous['parser_version'], previous['payload_sha256'],
                                                       previous['payload'], item['entry_id']))
            else:
                c.execute('delete from entry_dictionary_extraction where lexicon_entry_id=?', (item['entry_id'],))
            changed += 1
        if before != table_fingerprints(c):
            raise ValueError('rollback touched protected data')
        c.commit()
    return {'rolled_back': changed, 'learning_records_preserved': True}


def clone(source: Path, database: Path):
    database = database.resolve()
    if not database.is_relative_to(ROOT / 'test-artifacts') or database.exists():
        raise ValueError('clone requires new database in test-artifacts')
    assert_not_real_data(database, action='clone rehearsal into')
    database.parent.mkdir(parents=True, exist_ok=True)
    with read_only(source) as src, sqlite3.connect(database) as dst:
        src.backup(dst)
    return {'cloned_to': str(database)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('preview')
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--limit', type=int)
    p.add_argument('--review', type=Path)
    p = sub.add_parser('clone')
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--database', type=Path, required=True)
    p = sub.add_parser('apply')
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--plan-sha256', required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p = sub.add_parser('rollback')
    p.add_argument('--database', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'preview':
        result = preview(args.database, args.out, args.limit, args.review)
    elif args.command == 'clone':
        result = clone(args.source, args.database)
    elif args.command == 'apply':
        result = apply(args.database, args.plan, args.plan_sha256, args.receipt)
    else:
        result = rollback(args.database, args.receipt)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
