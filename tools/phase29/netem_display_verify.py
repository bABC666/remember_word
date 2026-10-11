"""Read-only acceptance of display-only plans, including retained raw sources."""
import argparse
import json
from collections import Counter
from pathlib import Path

import netem_display_diagnosis as diagnosis
import netem_rich_import as rich


def verify(database,plan_path,receipt_path,out):
    plan=json.loads(plan_path.read_bytes());receipt=json.loads(receipt_path.read_bytes())
    old=json.loads((out.parent/'diagnosis.json').read_bytes())
    report={'entries':len(plan['records']),'raw_payloads_preserved':True,
            'human_core_confirmations_added':0,'failures':[], 'pending':[], 'samples':[],
            'meaning_decision_kinds':Counter(), 'ipa_classifications':Counter(),
            'script_candidates':{'merged_same_context':[], 'retained_distinct_or_ambiguous_context':[]},
            'same_ancestor_candidates':{'resolved_equivalent_expressions':[],
                'same_explicit_context_pending':[], 'different_or_ambiguous_english_context':[]}}
    records={r['word']:r for r in plan['records']}
    undo_records={item['entry_id']:item for item in receipt['undo']}
    with rich.read_only(database) as c:
        c.execute('pragma temp_store=memory')
        report['protected_tables_preserved']=rich.table_fingerprints(c)==receipt['preserved_tables']
        report['integrity_check']=c.execute('pragma integrity_check').fetchone()[0]
        report['foreign_key_errors']=len(c.execute('pragma foreign_key_check').fetchall())
        for r in plan['records']:
            row=c.execute('select payload,payload_sha256 from entry_dictionary_extraction where lexicon_entry_id=?',(r['entry_id'],)).fetchone()
            actual=json.loads(row['payload'])
            if rich.digest(actual)!=r['payload_sha256'] or row['payload_sha256']!=r['payload_sha256']:
                report['failures'].append(r['word']+':payload mismatch')
            original=dict(actual);profile=original.pop('learning_display')
            if rich.digest(original)!=profile['baseline_sha256']:
                report['failures'].append(r['word']+':raw baseline changed')
            old_undo=undo_records.get(r['entry_id'])
            if old_undo and original!=json.loads(old_undo['before']['payload']):
                report['failures'].append(r['word']+':raw extraction not identical')
            for p in profile['pronunciations']:
                if p['text'].startswith('-') or p['text'].endswith('-'):
                    report['failures'].append(r['word']+':fragment exposed')
            report['meaning_decision_kinds'].update(d['kind'] for d in profile['decisions'])
            report['ipa_classifications'].update(d['classification'] for d in profile['held_pronunciations'])
            if (profile['unresolved_meanings'] or profile['pronunciation_pending_reason']
                    or profile.get('representative_pending_reason')):
                report['pending'].append({'word':r['word'],'entry_id':r['entry_id'],
                    'meaning_groups':profile['unresolved_meanings'],
                    'pronunciation_reason':profile['pronunciation_pending_reason'],
                    'representative_reason':profile.get('representative_pending_reason','')})
    for candidate in old['script_duplicates']:
        profile=records[candidate['word']]['payload']['learning_display']
        owners={member['locator']:i for i,s in enumerate(profile['senses']) for member in s['display_members']}
        collapsed=[len({owners.get(s['locator'],s['locator']) for s in group})==1 for group in candidate['groups']]
        if any(collapsed):report['script_candidates']['merged_same_context'].append(candidate['word'])
        if not all(collapsed):report['script_candidates']['retained_distinct_or_ambiguous_context'].append(candidate['word'])
    for candidate in old['same_gloss_candidates']:
        profile=records[candidate['word']]['payload']['learning_display']
        owners={member['locator']:i for i,s in enumerate(profile['senses']) for member in s['display_members']}
        resolved=all(len({owners.get(s['locator'],s['locator']) for s in group})==1 for group in candidate['groups'])
        key=('resolved_equivalent_expressions' if resolved else 'same_explicit_context_pending'
             if profile['unresolved_meanings'] else 'different_or_ambiguous_english_context')
        report['same_ancestor_candidates'][key].append(candidate['word'])
    for word in ['Easter','July','January','learn','and','or','you','February','yellow','bank','lead','read','record','set','double','lease']:
        r=records[word];p=r['payload'];profile=p['learning_display']
        report['samples'].append({'word':word,'before_senses':diagnosis.eligible(p,'senses'),
            'after_senses':profile['senses'],'pronunciations':profile['pronunciations'],
            'held_pronunciations':profile['held_pronunciations'],
            'originals':p['originals'],'new_original':profile['new_source_original']})
    report['passed']=not report['failures'] and report['protected_tables_preserved'] and report['integrity_check']=='ok' and report['foreign_key_errors']==0
    diagnosis.write_report(out,report)
    return {k:v for k,v in report.items() if k not in {'pending','samples','script_candidates','same_ancestor_candidates'}} | {
        'pending_words':len(report['pending']),
        'script_candidates':{k:len(v) for k,v in report['script_candidates'].items()},
        'same_ancestor_candidates':{k:len(v) for k,v in report['same_ancestor_candidates'].items()}}


def verify_ui(database,before_path,receipt_path,out,username):
    before=json.loads(before_path.read_bytes());receipt=json.loads(receipt_path.read_bytes())
    with rich.read_only(database) as c:
        c.execute('pragma temp_store=memory')
        actor=c.execute('select id from user where username=?',(username,)).fetchone()
        if not actor:raise ValueError('isolated QA actor missing')
        actor_id=actor[0]
        states={row['id']:dict(row) for row in c.execute('select * from user_word_state')}
        existing_preserved=all(states.get(row['id'])==row for row in before['states'])
        previous_ids={row['id'] for row in before['states']}
        added=[row for row in states.values() if row['id'] not in previous_ids]
        old_tables_preserved={}
        for name,expected in receipt['preserved_tables'].items():
            if name=='user_session':continue  # Clone startup prunes expired sessions, not learning data.
            quoted='"'+name.replace('"','""')+'"'
            columns={row[1] for row in c.execute(f'pragma table_info({quoted})')}
            where=''
            if name=='user':where=' where id != ?'
            elif 'user_id' in columns:where=' where user_id is null or user_id != ?'
            rows=[list(row) for row in c.execute(f'select * from {quoted}{where} order by rowid',(actor_id,) if where else ())]
            old_tables_preserved[name]={'rows':len(rows),'sha256':rich.digest(rows)}==expected
        reviews_unchanged=c.execute('select count(*) from review_event').fetchone()[0]==before['review_count']
        report={'all_preexisting_learning_states_ids_and_values_preserved':existing_preserved,
            'all_preexisting_business_tables_preserved_excluding_QA_and_auth_sessions':all(old_tables_preserved.values()),
            'preserved_tables':old_tables_preserved,'review_events_unchanged':reviews_unchanged,
            'new_QA_pending_states':len(added),'all_new_states_belong_to_QA':all(row['user_id']==actor_id and row['status']=='new' for row in added),
            'new_states_from_normal_queue_fill_only':len(added)<=500,
            'authentication_scope':'Only disposable QA login/logout; clone startup session housekeeping excluded',
            'formal_database_written':False}
        report['passed']=all([existing_preserved,all(old_tables_preserved.values()),reviews_unchanged,
                            report['all_new_states_belong_to_QA'],report['new_states_from_normal_queue_fill_only']])
    diagnosis.write_report(out,report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--database',type=Path,required=True);p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--receipt',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--ui-before',type=Path);p.add_argument('--qa-username',default='netem_rich_qa')
    a=p.parse_args()
    result=verify_ui(a.database,a.ui_before,a.receipt,a.out,a.qa_username) if a.ui_before else verify(a.database,a.plan,a.receipt,a.out)
    print(json.dumps(result,ensure_ascii=False,indent=2))
