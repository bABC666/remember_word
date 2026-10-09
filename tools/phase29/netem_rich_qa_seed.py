"""Create a disposable QA account in an explicitly isolated rich-import clone."""
import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    os.environ['VOCAB_DATA_DIR'] = str(database.parent)
    os.environ['VOCAB_REAL_DATA_DIR'] = str(ROOT / 'data')
    os.environ['VOCAB_TEST_MODE'] = '1'
    from app.testing_guards import assert_not_real_data
    assert_not_real_data(database, action='seed QA account in')
    if not database.is_relative_to(ROOT / 'test-artifacts'):
        raise ValueError('QA seed requires disposable test-artifacts clone')
    real = ROOT / 'data/vocab.db'
    if real.exists() and database.samefile(real):
        raise ValueError('production hardlink refused')
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.cli import create_user_account, set_password_for_user
    from app.db import make_engine
    from app.models import Lexicon, LexiconEntry, User, UserSettings
    from app.services.userdata import get_or_create_word_state
    secret_path = database.parent / 'qa-credentials.json'
    if secret_path.exists():
        raise FileExistsError(secret_path)
    password = secrets.token_urlsafe(24)
    engine = make_engine(f'sqlite:///{database.as_posix()}')
    with Session(engine, expire_on_commit=False) as s:
        if s.scalar(select(User).where(User.username == 'netem_rich_qa')) is None:
            create_user_account(s, 'netem_rich_qa', password, display_name='隔离词性测试')
        else:
            set_password_for_user(s, 'netem_rich_qa', password)
        user = s.scalar(select(User).where(User.username == 'netem_rich_qa'))
        lexicon = s.scalar(select(Lexicon).where(Lexicon.name == 'NETEM', Lexicon.source_type == 'netem'))
        settings = s.get(UserSettings, user.id)
        settings.selected_lexicon_id = lexicon.id
        ids = {}
        for i, word in enumerate(['play', 'abundant', 'above', 'absent', 'run', 'set', 'April']):
            entry = s.scalar(select(LexiconEntry).where(LexiconEntry.lexicon_id == lexicon.id,
                                                       LexiconEntry.word == word))
            state = get_or_create_word_state(s, user, entry)
            state.status = 'learning'
            state.next_review_at = datetime.now(UTC) - timedelta(days=10-i)
            ids[word] = entry.id
        s.commit()
    secret_path.write_text(json.dumps({'username': 'netem_rich_qa', 'password': password,
                                      'entry_ids': ids, 'lexicon_id': lexicon.id}), encoding='utf-8')
    print('QA account created only in isolated clone; credentials not logged.')


if __name__ == '__main__':
    main()
