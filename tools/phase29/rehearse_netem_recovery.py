"""Clone, apply twice and locally roll back a fingerprint-locked recovery plan."""
import argparse
import json
from pathlib import Path

import netem_rich_import as rich


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--plan-sha256',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    output=args.out.resolve()
    if not output.is_relative_to(rich.ROOT/'test-artifacts') or output.exists():
        raise ValueError('fresh rehearsal directory under test-artifacts required')
    database=output/'clone/vocab.db'
    rich.clone(args.source,database)
    with rich.read_only(database) as c:
        before=rich.table_fingerprints(c)
        previous={row['lexicon_entry_id']:row['payload_sha256'] for row in c.execute('select * from entry_dictionary_extraction')}
    receipt=output/'batch-undo.json'
    first=rich.apply(database,args.plan,args.plan_sha256,receipt)
    repeat=rich.apply(database,args.plan,args.plan_sha256,receipt)
    with rich.read_only(database) as c:
        assert rich.table_fingerprints(c)==before
        for row in json.loads(args.plan.read_bytes())['records']:
            actual=c.execute('select payload,payload_sha256 from entry_dictionary_extraction where lexicon_entry_id=?',(row['entry_id'],)).fetchone()
            assert actual['payload_sha256']==row['payload_sha256']==rich.digest(json.loads(actual['payload']))
        assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
        assert not c.execute('pragma foreign_key_check').fetchall()
    rich.clone(database,output/'updated/vocab.db')
    rollback=rich.rollback(database,receipt)
    rollback_repeat=rich.rollback(database,receipt)
    with rich.read_only(database) as c:
        assert rich.table_fingerprints(c)==before
        assert {row['lexicon_entry_id']:row['payload_sha256'] for row in c.execute('select * from entry_dictionary_extraction')}==previous
    report={'plan_sha256':args.plan_sha256,'apply':first,'repeat':repeat,'rollback':rollback,
            'rollback_repeat':rollback_repeat,'all_existing_tables_preserved':True,
            'all_original_extraction_rows_restored':True,'preserved_tables':before,
            'integrity_check':'ok','foreign_key_errors':0}
    (output/'preservation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='preserved_tables'},ensure_ascii=False))


if __name__=='__main__':
    main()
