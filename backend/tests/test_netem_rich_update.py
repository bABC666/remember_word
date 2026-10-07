import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import LexiconEntry

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('netem_rich_import', ROOT / 'tools/phase29/netem_rich_import.py')
rich = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rich)


def plan_for(world, tmp_path):
    lexicon = world.lexicon('NETEM')
    world.add_word('play', lexicon_id=lexicon)
    with world.session() as s:
        entry = s.scalar(select(LexiconEntry).where(LexiconEntry.word == 'play'))
        database = Path(s.bind.url.database)
        entry_id = entry.id
    with rich.read_only(database) as c:
        baseline = rich.original_baseline(c.execute('select * from lexicon_entry where id=?', (entry_id,)).fetchone())
    payload = {'senses': [], 'pronunciations': [], 'originals': []}
    plan = {'parser_version': rich.PARSER_VERSION, 'lexicon_id': lexicon, 'source_files': {},
            'records': [{'entry_id': entry_id, 'word': 'play', 'baseline_sha256': rich.digest(baseline),
                         'payload': payload, 'payload_sha256': rich.digest(payload)}]}
    path = tmp_path / 'plan.json'
    path.write_text(rich.canonical(plan), encoding='utf-8')
    return database, path, rich.sha(path.read_bytes()), entry_id


def test_apply_repeat_and_rollback_preserve_learning_added_after_apply(world, tmp_path):
    database, plan, checksum, _entry_id = plan_for(world, tmp_path)
    receipt = ROOT / 'test-artifacts/netem-rich-20261007' / (tmp_path.name + '-receipt.json')
    repeat = receipt.with_name(receipt.stem + '-repeat.json')
    try:
        assert rich.apply(database, plan, checksum, receipt)['changed'] == 1
        assert rich.apply(database, plan, checksum, receipt)['changed'] == 0
        assert rich.apply(database, plan, checksum, repeat)['changed'] == 0
        listed = world.client.get('/api/words').json()['words'][0]
        assert world.client.post(f'/api/study/word-states/{listed["word_state_id"]}/review',
                                 json={'result': 'know'}).status_code == 200
        with rich.read_only(database) as c:
            before = rich.table_fingerprints(c)
        assert rich.rollback(database, receipt)['rolled_back'] == 1
        assert rich.rollback(database, receipt)['rolled_back'] == 0
        with rich.read_only(database) as c:
            assert before == rich.table_fingerprints(c)
    finally:
        receipt.unlink(missing_ok=True)
        repeat.unlink(missing_ok=True)


def test_corrupt_plan_and_entry_drift_refuse_without_writes(world, tmp_path):
    database, path, _checksum, _entry_id = plan_for(world, tmp_path)
    receipt = ROOT / 'test-artifacts/netem-rich-20261007' / (tmp_path.name + '-drift.json')
    with pytest.raises(ValueError, match='checksum'):
        rich.apply(database, path, '0' * 64, receipt)
    plan = json.loads(path.read_bytes())
    plan['records'].append(dict(plan['records'][0], entry_id=999999))
    path.write_text(rich.canonical(plan), encoding='utf-8')
    with pytest.raises(ValueError, match='baseline drift'):
        rich.apply(database, path, rich.sha(path.read_bytes()), receipt)
    with rich.read_only(database) as c:
        assert c.execute('select count(*) from entry_dictionary_extraction').fetchone()[0] == 0
    assert not receipt.exists()


def test_production_and_backups_are_never_writable():
    from app.testing_guards import UnsafeDatabasePathError
    with pytest.raises((UnsafeDatabasePathError, ValueError)):
        rich.writable_isolated(ROOT / 'data/vocab.db')
    with pytest.raises((UnsafeDatabasePathError, ValueError)):
        rich.writable_isolated(ROOT / 'data/backups/manual-vocab.db')
