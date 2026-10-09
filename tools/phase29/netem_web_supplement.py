"""Bind the authorized91 online supplements to immutable dictionary originals.

Builds a full incremental plan from the preserved recovery candidate. The input
database is read-only; existing isolated/protected writers handle apply/rollback.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from urllib.parse import quote, urlparse

import netem_rich_import as rich
import netem_source_audit as audit

ROOT=rich.ROOT
BASE_SHA='29e90f367ea571773b64aedeb639bd59d01ac1c073088b0de2a4e53a3a11cb10'
VERSION='netem-web91-v1'
REVIEW_KIND='automated_online_source_semantic_comparison'
FORMS='standard spelling of|alternative spelling of|alternative form of|altsp|alt form|synonym of|syn of'
USAGE_LABELS={'archaic':'古旧','obsolete':'已废用','rare':'罕见','informal':'非正式','colloquial':'口语',
              'proscribed':'不推荐的用法','figurative':'比喻','figuratively':'比喻','ironic':'讽刺',
              'law':'法律','legal':'法律','computing':'计算机','military':'军事','chemistry':'化学',
              'British':'英式','US':'美式','UK':'英式','Scots law':'苏格兰法律','Philippines':'菲律宾',
              'biology':'生物学','psychology':'心理学','cosmetics':'化妆品','geometry':'几何学'}


def _english_line(text,line):
    lines=text.splitlines()
    if not isinstance(line,int) or not 1<=line<=len(lines):
        raise ValueError('source line outside original')
    if audit.wikitext_language_map(text).get(line)!='en':
        raise ValueError('citation outside English section')
    return lines[line-1]


def _chinese_fields(raw):
    values=[]
    for match in re.finditer(r'\{\{(?:t\+?|tt\+?)\|(?:zh|cmn)\|((?:\[\[[^\]]+\]\]|[^|{}])+)([^{}]*)\}\}',raw):
        values.append(audit.render_definition_markup(match[1])[0])
        alt=re.search(r'\|alt=([^|{}]+)',match[2])
        if alt:values.append(audit.render_definition_markup(alt[1])[0])
    return values


def _leading_labels(definition,text):
    """Only prefix labels govern the whole selected definition clause.

    Later labels may govern a different clause on the same line. A qualifier
    such as 'also figuratively' must not make the ordinary meaning figurative.
    """
    remaining=re.sub(r'^#+\s*','',definition)
    labels=[]
    while True:
        match=re.match(r'^\{\{([^{}]+)\}\}\s*',remaining)
        if not match:break
        args=match[1].split('|')
        if args[0] in {'sid','senseid'} and len(args)>1 and args[1]=='en':
            remaining=remaining[match.end():];continue
        if args[0] not in {'lb','label'} or len(args)<3 or args[1]!='en':break
        tokens=args[2:]
        for token in tokens:
            label=USAGE_LABELS.get(token)
            if token in {'figurative','figuratively'} and 'also' in tokens:label='亦可用于比喻'
            if token=='informal' and 'usually' in tokens:label='通常为非正式用法'
            if label and label not in text and label not in labels:labels.append(label)
        remaining=remaining[match.end():]
    return labels


def _source(page):
    domain='en.wiktionary.org' if page['family']=='enwiktionary' else 'zh.wiktionary.org'
    url=f'https://{domain}/w/index.php?title={quote(page["title"])}&oldid={page["oldid"]}'
    modifications=('选取固定英语小节的定义及其明确中文翻译；直接原译仅去链接等格式标记。'
                   '没有正确中文原译时忠实翻译引用的英文定义并标为据英文来源翻译整理。'
                   '不取例句、词源、其它语言内容，不按分号拆造义项，不确认人工核心释义。')
    return {'name':'online-wiktionary-91-fixed-revisions','family':page['family'],
        'publisher':'英文维基词典贡献者' if page['family']=='enwiktionary' else '中文维基词典贡献者',
        'version':'oldid '+page['oldid']+' / '+page.get('timestamp',''),
        'revision':page['oldid'],'file':page['file'],'original_file_sha256':page['file_sha256'],
        'body_sha256':page['body_sha256'],'source_page_title':page['title'],'license_id':'CC-BY-SA-4.0',
        'attribution':{'creators':'维基词典贡献者；固定页面及其编辑历史列出作者',
            'license_url':'https://creativecommons.org/licenses/by-sa/4.0/','source_url':url,
            'snapshot_url':url,'snapshot_label':page['title']+' / oldid '+page['oldid'],
            'snapshot_sha256':page['file_sha256'],'modifications':modifications,
            'disclaimer':'来源按原样提供；自动来源对照不属于人工核心确认。原页中另标的外部引文不改变其原许可，本次义项不采用这些引用。',
            'links':[{'label':'贡献者历史','url':f'https://{domain}/w/index.php?title={quote(page["title"])}&action=history'},
                     {'label':'词典许可与外部材料条件','url':f'https://{domain}/wiki/Wiktionary:Copyrights'}]},
        'extraction_modifications':modifications}


def bind_choice(word,choice,pages):
    key=(choice['family'],choice['title'])
    page=pages.get(key)
    if not page or str(choice['oldid'])!=page['oldid']:
        raise ValueError('fixed source title/revision mismatch')
    if page['family']!='enwiktionary':
        raise ValueError('this supplement uses verified English Wiktionary originals only')
    definition=_english_line(page['text'],choice['definition_line'])
    if not re.match(r'^#+\s*[^:#*;]',definition):
        raise ValueError('selected line is not an English definition')
    if re.search(r'\{\{(?:ux|uxi|usex|quote[^|{}]*|RQ:[^|{}]+)\|',definition):
        raise ValueError('example/quotation is not definition evidence')
    pos,raw_pos,pos_line=audit.english_context(page['text'],choice['definition_line'])
    if not pos or choice['pos_key']!=pos:
        raise ValueError('source POS mismatch')
    stack=[]
    for number,raw in enumerate(page['text'].splitlines()[:choice['definition_line']],1):
        heading=audit.HEADING.fullmatch(raw.strip())
        if heading:
            level=len(heading[1]);title=heading[2].strip()
            stack=[item for item in stack if item[0]<level]+[(level,title,number)]
    if any(number>pos_line and audit.ANCILLARY.search(title) for _level,title,number in stack):
        raise ValueError('ancillary list is not definition evidence')
    # A same-page form-only marker is a pointer, not a substitute for its meaning.
    form_only=re.search(r'\{\{(?:'+FORMS+r')\|en\|',definition)
    if form_only:
        raise ValueError('form-only definition needs the actual relation target')
    relations=[]
    if choice['title']!=word:
        declared=choice.get('form_relation',[])
        if len(declared)!=1 or declared[0].get('source_title')!=word or declared[0].get('target_title')!=choice['title']:
            raise ValueError('explicit source form relation required')
        relation=declared[0]
        origin=pages.get((choice['family'],word))
        if origin is None:raise ValueError('missing form relation original')
        raw=_english_line(origin['text'],relation['line'])
        match=re.search(r'\{\{(?:'+FORMS+r')\|en\|([^{}]+)\}\}',raw)
        if not match or choice['title'] not in [arg.strip() for arg in match[1].split('|') if '=' not in arg]:
            raise ValueError('form relation target not declared in original')
        if audit.english_context(origin['text'],relation['line'])[0]!=pos:
            raise ValueError('form relation POS mismatch')
        relations.append({**relation,'oldid':origin['oldid'],'raw_text':raw,'body_sha256':origin['body_sha256']})
    elif choice.get('form_relation'):
        raise ValueError('unexpected form relation')
    if choice['meaning_kind'] not in {'source','derived'} or not str(choice.get('note','')).strip():
        raise ValueError('explicit source/derived review required')
    text=audit.render_definition_markup(choice['text'])[0]
    if not audit.HAN.search(text) or re.search(r'\{\{|\}\}|-\{|<[^>]+>',text):
        raise ValueError('unusable Chinese review value')
    translation_line=choice.get('translation_line')
    if choice['meaning_kind']=='source':
        raw_translation=_english_line(page['text'],translation_line)
        if (audit.english_context(page['text'],translation_line)[0]!=pos
                or text not in _chinese_fields(raw_translation)):
            raise ValueError('Chinese source field/POS not matched')
    elif translation_line is not None:
        raise ValueError('derived value must not claim an original Chinese translation')
    lines=choice['citation_lines']
    if (not lines or len(set(lines))!=len(lines) or choice['definition_line'] not in lines
            or (translation_line is not None and translation_line not in lines)):
        raise ValueError('complete source citations required')
    citations=[{'source_title':page['title'],'line':line,'raw_text':_english_line(page['text'],line)} for line in lines]
    labels=_leading_labels(definition,text)
    return {'text':text,'review_selected_text':choice['text'],'language':'en','status':'source_verified','reason':'',
        'pos_key':pos,'pos_label':audit.POS_LABELS[pos],'pos_raw':raw_pos,'pos_locator':str(pos_line),
        'meaning_kind':choice['meaning_kind'],'semantic_scope':'sense',
        'usage_labels':choice.get('usage_labels',[]),'display_usage_labels':labels,
        'definition_line':choice['definition_line'],
        'translation_line':translation_line,'locator':f'enwiktionary:{page["oldid"]}:L'+','.join(map(str,lines)),
        'raw_text':'\n'.join(f'L{item["line"]}: {item["raw_text"]}' for item in citations),
        'source_citations':citations,'form_relation':relations,'source':_source(page),
        'quality_review':{'kind':REVIEW_KIND,'audit_version':audit.AUDIT_VERSION,'meaning_result':'accept',
            'pos_result':'accept','note':choice['note'],'core_confirmation':False}}


def load_sources(evidence):
    pages={};ledger={}
    for folder_name in ['enwiktionary','enwiktionary-relations','zhwiktionary']:
        folder=(evidence/folder_name).resolve()
        manifest_path=folder/'manifest.json'
        manifest=json.loads(manifest_path.read_bytes())
        family=manifest['family']
        if family not in {'enwiktionary','zhwiktionary'} or manifest['license']!='CC-BY-SA-4.0':
            raise ValueError('unknown source conditions')
        domain='en.wiktionary.org' if family=='enwiktionary' else 'zh.wiktionary.org'
        verified={}
        for record in manifest['responses']:
            path=(folder/record['file']).resolve()
            parsed=urlparse(record['url'])
            if not path.is_relative_to(folder) or parsed.scheme!='https' or parsed.netloc!=domain or parsed.path!='/w/api.php':
                raise ValueError('unrecognized source snapshot location')
            raw=path.read_bytes()
            if rich.sha(raw)!=record['sha256']:raise ValueError('online source fingerprint mismatch')
            ledger[path.relative_to(ROOT).as_posix()]={'sha256':record['sha256'],'bytes':len(raw)}
            for item in json.loads(raw).get('query',{}).get('pages',[]):
                for revision in item.get('revisions',[]):
                    body=revision['slots']['main']['content']
                    verified[item['title']]={'title':item['title'],'oldid':str(revision['revid']),
                        'text':body,'body_sha256':rich.sha(body.encode()),'timestamp':revision.get('timestamp'),
                        'file':path.relative_to(ROOT).as_posix(),'file_sha256':record['sha256'],'family':family}
        pages_path=folder/'pages.json'
        saved=json.loads(pages_path.read_bytes())
        if {key:{k:v for k,v in value.items() if k!='family'} for key,value in verified.items()}!=saved:
            raise ValueError('saved source page/revision/body drift')
        for key,value in verified.items():
            if (family,key) in pages:raise ValueError('duplicate source title')
            pages[(family,key)]=value
        for path in [manifest_path,pages_path]:
            ledger[path.relative_to(ROOT).as_posix()]={'sha256':rich.sha(path.read_bytes()),'bytes':path.stat().st_size}
    policy=pages[('enwiktionary','Wiktionary:Copyrights')]['text']
    if '4.0' not in policy or 'ShareAlike' not in policy:
        raise ValueError('actual English dictionary licence not established')
    return pages,ledger


def preview(database,base_path,evidence,reviews,output,corrections_path=None,pilot=False):
    output=output.resolve();evidence=evidence.resolve()
    if (not output.is_relative_to(ROOT/'test-artifacts') or output.exists()
            or not evidence.is_relative_to(ROOT/'test-artifacts') or rich.sha(base_path.read_bytes())!=BASE_SHA):
        raise ValueError('fresh isolated output and fixed recovery base required')
    base=json.loads(base_path.read_bytes());ledger=copy.deepcopy(base['source_files'])
    for name,meta in ledger.items():
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT/'test-artifacts') or rich.sha(path.read_bytes())!=meta['sha256']:
            raise ValueError('recovery base evidence changed')
    ledger[base_path.resolve().relative_to(ROOT).as_posix()]={'sha256':BASE_SHA,'bytes':base_path.stat().st_size}
    targets_path=evidence/'targets.json';targets=json.loads(targets_path.read_bytes())
    expected={r['entry_id']:r['word'] for r in base['records'] if not r['changes']['display_sense_count']}
    if {r['entry_id']:r['word'] for r in targets}!=expected or len(targets)!=len(expected):
        raise ValueError('remaining target identity drift')
    pages,files=load_sources(evidence);ledger.update(files)
    decisions={}
    for path in reviews:
        path=path.resolve()
        if not path.is_relative_to(ROOT/'test-artifacts'):raise ValueError('review must stay under test-artifacts')
        raw=path.read_bytes();review=json.loads(raw)
        if review.get('review_kind')!=REVIEW_KIND or review.get('core_confirmation') is not False:
            raise ValueError('online review must be automated without core confirmation')
        for item in review['records']:
            if item['entry_id'] in decisions or expected.get(item['entry_id'])!=item['word'] or not item['values']:
                raise ValueError('duplicate/empty/stale online word review')
            decisions[item['entry_id']]=item
        ledger[path.relative_to(ROOT).as_posix()]={'sha256':rich.sha(raw),'bytes':len(raw)}
    if set(decisions)!=set(expected):raise ValueError('complete remaining-word review required')
    if corrections_path:
        corrections_path=corrections_path.resolve()
        if not corrections_path.is_relative_to(ROOT/'test-artifacts'):
            raise ValueError('correction must stay under test-artifacts')
        raw=corrections_path.read_bytes();review=json.loads(raw)
        if review.get('review_kind')!=REVIEW_KIND or review.get('core_confirmation') is not False:
            raise ValueError('invalid online review correction')
        seen=set()
        for correction in review['corrections']:
            entry_id,index=correction['entry_id'],correction['value_index']
            if (entry_id,index) in seen or entry_id not in decisions:
                raise ValueError('duplicate/unknown online review correction')
            seen.add((entry_id,index));record=decisions[entry_id]
            if (record['word']!=correction['word'] or not 0<=index<len(record['values'])
                    or rich.digest(record['values'][index])!=correction['previous_choice_sha256']):
                raise ValueError('stale online review correction')
            record['values'][index]=correction['choice']
        ledger[corrections_path.relative_to(ROOT).as_posix()]={'sha256':rich.sha(raw),'bytes':len(raw)}
    ledger[targets_path.relative_to(ROOT).as_posix()]={'sha256':rich.sha(targets_path.read_bytes()),'bytes':targets_path.stat().st_size}
    records=copy.deepcopy(base['records'])
    with rich.read_only(database) as c:
        current=c.execute('select * from lexicon_entry where lexicon_id=?',(base['lexicon_id'],)).fetchall()
        if {r['id'] for r in current}!={r['entry_id'] for r in records}:raise ValueError('NETEM IDs changed')
        by_id={r['id']:r for r in current}
        for record in records:
            entry=by_id[record['entry_id']]
            existing=c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?',(record['entry_id'],)).fetchone()
            if (rich.digest(rich.original_baseline(entry))!=record['baseline_sha256'] or existing is None
                    or existing['payload_sha256'] not in {record['baseline_extraction_sha256'],record['payload_sha256']}
                    or rich.digest(json.loads(existing['payload']))!=existing['payload_sha256']):
                raise ValueError('entry/extraction baseline drift')
            record['baseline_extraction_sha256']=existing['payload_sha256']
            if record['entry_id'] not in decisions:continue
            selected=decisions[record['entry_id']]
            values=[bind_choice(record['word'],choice,pages) for choice in selected['values']]
            payload=record['payload'];payload['senses']=values+payload['senses']
            used={(v['source']['family'],v['source']['source_page_title']) for v in values}
            if any(v['form_relation'] for v in values):used.add(('enwiktionary',record['word']))
            for key in sorted(used):
                page=pages[key];source=_source(page)
                payload['originals'].append({'text':page['text'],'headword':page['title'],'revision':page['oldid'],
                    'file':page['file'],'file_sha256':page['file_sha256'],'body_sha256':page['body_sha256'],'source':source})
                if page['title']==record['word']:
                    for p in audit.parse_wikitext(page['text'])['pronunciations']:
                        p.update(source=source,locator=f'enwiktionary:{page["oldid"]}:L{p["locator"]}')
                        payload['pronunciations'].append(audit.audit_pronunciation(p))
            payload['audit']['online_supplement']={'version':VERSION,'base_plan_sha256':BASE_SHA,'review_kind':REVIEW_KIND,'core_confirmation':False}
            payload['failures']=[failure for failure in payload.get('failures',[]) if failure!='no_structurally_usable_chinese_sense']
            shown=[s for s in payload['senses'] if s['status']=='source_verified']
            record['changes'].update(new_pos=sorted({s['pos_key'] for s in shown if s['pos_key']}),
                display_pos=sorted({s['pos_key'] for s in shown if s['pos_key']}),display_sense_count=len(shown),
                recovered_sense_count=len(shown),multiple_senses=len(shown)>1,
                display_ungrouped_sense_count=sum(not s['pos_key'] for s in shown),online_values_added=len(values),
                online_derived_values=sum(v['meaning_kind']=='derived' for v in values),
                pronunciation_records=len(payload['pronunciations']),
                usable_pronunciations=sum(p['status']=='source_verified' for p in payload['pronunciations']))
            record['payload_sha256']=rich.digest(payload)
    if pilot:
        priority={*rich.REQUIRED,'mean','grown-up'}
        records=[record for record in records if record['entry_id'] in expected or record['word'] in priority]
        if len(records)!=100:raise ValueError('pilot must cover91 gaps and9 representative entries')
    coverage=rich.coverage(records)
    coverage.update(audit_version=audit.AUDIT_VERSION,online_supplement_version=VERSION,
        without_usable_chinese_sense=sum(not r['changes']['display_sense_count'] for r in records),
        online_words_added=len(decisions),online_values_added=sum(len(r['values']) for r in decisions.values()),
        online_derived_values=sum(v['meaning_kind']=='derived' for r in decisions.values() for v in r['values']),
        without_verified_pos=sum(not r['changes']['new_pos'] for r in records),
        words_with_verified_ungrouped_meanings=sum(r['changes']['display_ungrouped_sense_count']>0 for r in records))
    plan={**base,'records':records,'source_files':ledger,'coverage':coverage,'online_supplement_version':VERSION}
    output.mkdir(parents=True)
    (output/'plan.json').write_text(rich.canonical(plan)+'\n',encoding='utf-8')
    (output/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'online91.json').write_text(json.dumps([r for r in records if r['entry_id'] in decisions],ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'per-word.jsonl').write_text(''.join(rich.canonical({'entry_id':r['entry_id'],'word':r['word'],**r['changes']})+'\n' for r in records),encoding='utf-8')
    return {'plan_sha256':rich.sha((output/'plan.json').read_bytes()),**coverage}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--base-plan',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--review',type=Path,action='append',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--corrections',type=Path)
    parser.add_argument('--pilot',action='store_true')
    args=parser.parse_args()
    print(json.dumps(preview(args.database,args.base_plan,args.evidence,args.review,args.out,args.corrections,args.pilot),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
