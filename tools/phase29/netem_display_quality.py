"""Source-bound learning projection. Raw extraction and human confirmation stay intact."""
from __future__ import annotations
import argparse
import copy
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
import netem_rich_import as rich
import netem_display_diagnosis as diagnosis
from app.services.rich_dictionary_parser import Tree, Node, HEADING
import netem_pronunciation_context as pronunciation_context

VERSION = 'netem-learning-display-v2'
digest = rich.digest
RESTRICTED = r'(?i)weak|unstressed|dated|obsolete|uncommon|rare|nonstandard|folk|Early Modern|dialect|archaic|historical|spelling pronunciation|less common|minority|proscribed|deprecated|some speakers'


def sense_context(body, sense):
    tree = Tree(); tree.feed(body)
    nodes = {n.path:n for n in tree.root.descendants()}
    node = nodes.get(sense.get('sense_path'))
    if node is None:
        return {'path':sense.get('sense_path',''), 'glosses':[], 'reason':'missing_source_node'}
    # Find nearest English-bearing sense or the body root. Do not use a common
    # /ol ancestor: it contains distinct English senses (learn, and, you).
    while node.parent and not (node.tag == 'li' and re.search('[A-Za-z]', node.text(True))):
        node = node.parent
        if node.parent == tree.root:
            break
    glosses = []
    for child in node.children:
        if isinstance(child, str) and re.search('[A-Za-z]', child):
            value = child.strip()
            if value:
                glosses.append(value)
        elif isinstance(child, Node) and child.tag == 'ol':
            for li in child.children:
                if isinstance(li, Node) and li.tag == 'li' and re.search('[A-Za-z]', li.text(True)):
                    glosses.append(li.text(True))
    return {'path':node.path, 'glosses':glosses,
            'reason':'' if len(glosses) == 1 else 'shared_or_missing_english_gloss'}


def templates(line):
    return pronunciation_context.templates(line)


def split_parameters(text):
    return pronunciation_context.split_parameters(text)


def pronunciations(text, word):
    return pronunciation_context.parse(text, word)


def load_evidence(folder):
    folder = folder.resolve()
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    pages, ledger = {}, {manifest_path.relative_to(rich.ROOT).as_posix():
                        {'sha256':rich.sha(manifest_path.read_bytes())}}
    copyright_text = ''
    for record in manifest['files']:
        path = (folder / record['file']).resolve()
        if not path.is_relative_to(folder.resolve()) or rich.sha(path.read_bytes()) != record['sha256']:
            raise ValueError('online source fingerprint drift')
        ledger[path.relative_to(rich.ROOT).as_posix()] = {'sha256':record['sha256']}
        for page in json.loads(path.read_bytes())['query']['pages']:
            if 'revisions' not in page:
                continue
            revision = page['revisions'][0]
            value = {'title':page['title'], 'revision':revision['revid'],
                     'text':revision['slots']['main']['content'], 'record':record}
            pages[page['title']] = value
            if page['title'] == 'Wiktionary:Copyrights':
                copyright_text = value['text']
    if not ('4.0' in copyright_text and 'Creative Commons' in copyright_text):
        raise ValueError('fixed reuse conditions not established')
    return pages, ledger


def source_for(page):
    url = 'https://en.wiktionary.org/w/index.php?title=' + quote(page['title']) + '&oldid=' + str(page['revision'])
    return {'family':'enwiktionary', 'publisher':'Wiktionary contributors', 'version':str(page['revision']),
            'revision':str(page['revision']), 'original_file_sha256':page['record']['sha256'],
            'body_sha256':rich.sha(page['text'].encode()), 'obtained_at_utc':page['record']['fetched_at'],
            'attribution':{'creators':'English Wiktionary contributors; contributor history linked below',
                'license_url':'https://creativecommons.org/licenses/by-sa/4.0/', 'source_url':url,
                'modifications':'本项目按固定英语发音标题和 IPA 模板选择与分组，原文保留；不新增人工核心确认。',
                'links':[{'label':'贡献者历史','url':'https://en.wiktionary.org/w/index.php?title='+quote(page['title'])+'&action=history'},
                         {'label':'使用条件','url':'https://en.wiktionary.org/wiki/Wiktionary:Copyrights'}]},
            'license_id':'CC-BY-SA-4.0'}


def ipa_key(text):
    # Not semantic normalization: only optional syllable dots and affricate ties.
    return text.replace('.', '').replace('\u0361', '').replace('\u035c', '')


def pronunciation_context_lines(text):
    english=False;in_pron=False;lines=[]
    for number,line in enumerate(text.splitlines(),1):
        heading=HEADING.fullmatch(line.strip())
        if heading:
            if len(heading[1])==2:
                english=heading[2].strip()=='English'
            in_pron=english and bool(re.fullmatch(r'Pronunciation(?: \d+)?',heading[2].strip()))
        elif in_pron:
            lines.append((number,line))
    return lines


def confirmed_fragment_context(word,text,discussion):
    if text.startswith('-') or text.endswith('-'):return True
    # IPAchar also carries complete alternatives (forget, grocer). Its syntax is
    # never evidence of a fragment. These two explicitly reviewed discussions
    # identify individual coalescing sounds or a February syllable sequence.
    if word=='you' and text in {'d','dʒ','s','t','tʃ','z','ʃ','ʒ'}:
        return any('Following a word ending in an' in d['raw_text'] and 'may coalesce' in d['raw_text'] for d in discussion)
    if word=='February' and text in {'j','ɹi','ɹə.ɹi'}:
        return any('the first' in d['raw_text'] and 'sequence' in d['raw_text'] and 'haplology' in d['raw_text'] for d in discussion)
    return False


def choose_pronunciations(values):
    groups=defaultdict(list);result=[];seen=set()
    for original in values:
        key=(original['pronunciation_scope'],original['notation'],ipa_key(original['text']),tuple(original['display_labels']))
        if key in seen:continue
        seen.add(key)
        accents=set(original.get('accent_labels',[]))
        families=[]
        if accents & {'RP','UK','SSB','SSBE','Received Pronunciation'}:families.append('UK')
        if accents & {'GA','US','GenAm','General American'}:families.append('US')
        value=dict(original,accent_families=families,display_accent=','.join(families),representative=False)
        labels=','.join(value.get('qualifier_labels',[]))
        weak=bool(re.search(r'(?i)weak|unstressed',labels))
        restricted=bool(re.search(RESTRICTED,labels) or value.get('label_binding_pending'))
        special=any(re.search(r'(?i)th-fronting|l-vocali|t-glottali|monophthong|ungliding',v) for v in value.get('feature_labels',[]))
        usage=tuple(v for v in value.get('qualifier_labels',[]) if re.search(r'(?i)\b(noun|verb|adjective|adverb|past|present|plural|singular|participle)\b',v))
        result.append(value)
        for family in families:
            groups[(value['pronunciation_scope'],family,weak,usage)].append((value,restricted or special))
    for (_,_,weak,_),group in groups.items():
        candidates=[v for v,restricted in group if not restricted and v['notation']=='phonemic']
        if candidates and not weak:candidates[0]['representative']=True
    for scope in {v['pronunciation_scope'] for v in result}:
        if any(v['representative'] for v in result if v['pronunciation_scope']==scope):continue
        defaults=[v for v in result if v['pronunciation_scope']==scope and v['notation']=='phonemic' and not v['accent_labels'] and not v['feature_labels'] and not re.search(RESTRICTED,','.join(v['qualifier_labels']))]
        if len({ipa_key(v['text']) for v in defaults})==1:
            defaults[0]['representative']=True
            continue
    return result


# Explicit, contextual equivalence decisions. All other non-script alternatives
# remain candidates; this allowlist does not propagate by string similarity.
EQUIVALENTS = {
    ('July','seventh month of the Gregorian calendar'): {'七月','7月'},
    ('January','first month of the Gregorian calendar'): {'一月','元月'},
    ('or','conjunction'): {'或者','或'},
    ('yellow','having yellow as its color'): {'黄色','黄'},
    ('yellow','color'): {'黄色','黄'},
}


def projection(row, page):
    p = row['payload']; senses = diagnosis.eligible(p,'senses')
    originals = {o['body_sha256']:o for o in p['originals']}
    groups = {}; decisions = []; contexts = {}; candidates = defaultdict(list)
    for s in senses:
        original = originals.get(s['source'].get('body_sha256'))
        context = (sense_context(original['text'],s) if original and s['source']['family']=='wikdict'
                   else {'path':s['locator'],'glosses':[], 'reason':'non_wikdict_separate_scope'})
        scope = (s['source'].get('body_sha256'),context['path'],tuple(context['glosses']),
                 s.get('pos_key',''),tuple(s.get('display_usage_labels',[])))
        normalized = diagnosis.simplified(s['text'])
        # Within a source translation node, collapse exact script forms only.
        parts = s.get('translations', [s['text']])
        norm_parts = list(dict.fromkeys(diagnosis.simplified(part) for part in parts))
        if len(norm_parts)==1:
            normalized = norm_parts[0]
        # Do not split ordinary phrases by whitespace; only equal script copies.
        tokens = normalized.split()
        if len(tokens)>1 and len(set(tokens))==1:
            normalized=tokens[0]
        synonym = EQUIVALENTS.get((row['word'],context['glosses'][0]),set()) if len(context['glosses'])==1 else set()
        if normalized in synonym:
            normalized = next(v for v in ['七月','一月','或者','黄色'] if v in synonym)
        safe = bool(context['glosses'])
        key = (scope,normalized) if safe else (scope,s['locator'],normalized)
        projected = copy.deepcopy(s)
        projected.update(text=normalized, source_context=context, display_aliases=[],
                         display_members=[{'text':s['text'],'locator':s['locator'],'source':s['source']}])
        if key in groups:
            target = groups[key]
            target['display_members'].extend(projected['display_members'])
            if s['text'] != target['text']:
                target['display_aliases'].append(s['text'])
            decisions.append({'kind':'context_bound_equivalence','locator':s['locator'],
                              'target':target['locator'],'context':context,'original':s['text'],'display':target['text']})
        else:
            groups[key]=projected
            if normalized != s['text']:
                decisions.append({'kind':'script_display_normalization','locator':s['locator'],
                                  'original':s['text'],'display':normalized,'context':context})
        candidates[scope].append(s['text'])
        contexts[s['locator']]=context
    unresolved = [{'context':list(k[:3]), 'expressions':v,
                   'reason':'multiple_expressions_not_semantically_adjudicated'} for k,v in candidates.items()
                  if len(set(diagnosis.simplified(x) for x in v))>1 and
                  not (len(k[2])==1 and (row['word'],k[2][0]) in EQUIVALENTS)]
    display = list(groups.values())
    # Retain different English scopes even when the simplified Chinese is equal.
    by_text = defaultdict(list)
    for s in display:
        translation_set = tuple(sorted(diagnosis.simplified(t) for t in s.get('translations',[s['text']])))
        by_text[(s.get('pos_key'),translation_set,tuple(s.get('display_usage_labels',[])))].append(s)
    for vs in by_text.values():
        if len(vs)>1:
            for s in vs:
                s['display_context_note']='；'.join(s['source_context']['glosses']) or '来源义项对应仍待核'
    for s in display:
        glosses=s['source_context']['glosses']
        if len(glosses)==1 and glosses[0].startswith(('subject pronoun:','object pronoun:')):
            s['display_context_note']= '主语用法' if glosses[0].startswith('subject') else '宾语用法'
    fresh = []
    if page:
        source = source_for(page)
        fresh = pronunciations(page['text'],row['word'])
        for v in fresh:
            v['source']=source; v['locator']=f'enwiktionary:{page["revision"]}:'+v['locator'].split(':',1)[1]
        fresh=choose_pronunciations(fresh)
    held=[]
    for value in p.get('pronunciations',[]):
        if value.get('language')!='en':
            continue
        matches=[v for v in fresh if ipa_key(v['text'])==ipa_key(value['text'])]
        discussion = [dict(line=n,raw_text=line) for n,line in pronunciation_context_lines(page['text'])
                      if page and re.search(r'\{\{IPAchar\|[/\[]'+re.escape(value['text'])+r'[/\]]\}\}',line)] if page else []
        classification = ('corroborated_full_word' if matches else 'confirmed_non_word_fragment'
                          if confirmed_fragment_context(row['word'],value['text'],discussion) else 'insufficient_full_word_context')
        held.append({'locator':value['locator'],'text':value['text'],
                     'classification':classification,'discussion_evidence':discussion,
                     'matches':[v['locator'] for v in matches],
                     'reason':'原文对应不等于完整单词读音；仅固定 Pronunciation 中显式 IPA|en 获准进入学习展示。'})
    return {'version':VERSION,'baseline_sha256':digest(p),'core_confirmation':False,
            'senses':display,'pronunciations':fresh,'held_pronunciations':held,
            'decisions':decisions,'unresolved_meanings':unresolved,
            'new_source_original':{'raw_text':page['text'],'source':source_for(page)} if page else None,
            'representative_pending_reason': 'no_safe_representative_for_labelled_or_multiple_variants'
                if fresh and not any(v['representative'] for v in fresh) else '',
            'pronunciation_pending_reason':'' if fresh else 'no_explicit_full_word_IPA_in_accessible_fixed_revision'}


def preview(database, evidence, out, limit):
    out=out.resolve()
    if out.exists() or not out.is_relative_to(rich.ROOT/'test-artifacts'):
        raise ValueError('fresh preview under test-artifacts required')
    rows=diagnosis.records(database); pages, ledger=load_evidence(evidence)
    wik,_,original_ledger=rich.local_sources()
    ledger={**original_ledger,**ledger}
    required={'Easter','July','January','question','learn','or','and','yellow','you','February',
              'bank','lead','record','read','wind','bow','set','fine','double','lease',
              'plant','grow','forget','grocer','tomato','potato','wit','comparable',
              'with','can','fan','thin','could','yourselves','compose','portrait'}
    rows.sort(key=lambda r:(r['word'] not in required,digest(r['word'])))
    if limit:
        rows=rows[:limit]
    plan_records=[]; previews=[]
    with rich.read_only(database) as c:
        for row in rows:
            before=copy.deepcopy(row['payload']); before.pop('learning_display',None)
            for original in before['originals']:
                if original['source']['family']=='wikdict' and not any(
                    item['body_sha256']==original['body_sha256'] and item['text']==original['text']
                    for item in wik[row['word']]):
                    raise ValueError('WikDict original differs from fingerprinted archive')
            row['payload']=before
            profile=projection(row,pages.get(row['word']))
            after=copy.deepcopy(before);after['learning_display']=profile
            entry=c.execute('select * from lexicon_entry where id=?',(row['id'],)).fetchone()
            existing=c.execute('select * from entry_dictionary_extraction where lexicon_entry_id=?',(row['id'],)).fetchone()
            plan_records.append({'entry_id':row['id'],'word':row['word'],
                'baseline_sha256':digest(rich.original_baseline(entry)),
                'baseline_extraction_sha256':existing['payload_sha256'],
                'payload':after,'payload_sha256':digest(after),
                'changes':{'display_only':True}})
            previews.append({'entry_id':row['id'],'word':row['word'], 'before_senses':diagnosis.eligible(before,'senses'),
                'before_pronunciations':diagnosis.eligible(before,'pronunciations'),'after':profile})
    out.mkdir(parents=True)
    plan={'parser_version':rows[0]['payload'].get('parser_version','netem-rich-v1'),
          'display_version':VERSION,'lexicon_id':rows[0]['lexicon_id'],
          'evidence_directory':evidence.resolve().relative_to(rich.ROOT).as_posix(),
          'source_files':ledger,'records':plan_records,'core_confirmation':False}
    # Existing application stores parser version in its own column; preserve it.
    with rich.read_only(database) as c:
        plan['parser_version']=c.execute('select parser_version from entry_dictionary_extraction where lexicon_entry_id=?',
                                        (rows[0]['id'],)).fetchone()[0]
    (out/'plan.json').write_text(rich.canonical(plan),encoding='utf-8')
    (out/'preview.json').write_text(rich.canonical(previews),encoding='utf-8')
    stats={'entries':len(rows),'meaning_projection_coverage':sum(bool(r['after']['senses']) for r in previews),
           'pronunciation_coverage':sum(bool(r['after']['pronunciations']) for r in previews),
           'representative_coverage':sum(any(p['representative'] for p in r['after']['pronunciations']) for r in previews),
           'display_transformations':sum(len(r['after']['decisions']) for r in previews),
           'normalized_values':sum(d['kind']=='script_display_normalization' for r in previews for d in r['after']['decisions']),
           'merged_source_values':sum(d['kind']=='context_bound_equivalence' for r in previews for d in r['after']['decisions']),
           'semantic_candidate_words':sum(bool(r['after']['unresolved_meanings']) for r in previews),
           'original_ipa_classifications':dict(Counter(v['classification'] for r in previews for v in r['after']['held_pronunciations'])),
           'human_core_confirmations_added':0,'plan_sha256':rich.sha((out/'plan.json').read_bytes())}
    (out/'summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 逐词展示预览','', '自动来源与结构整理；人工核心确认新增 0。不同英文义项不合并。','']
    for r in previews:
        profile=r['after'];lines += ['## '+r['word'], '',
            '释义：'+' | '.join((s.get('pos_label') or '词性未定')+'：'+s['text']+
                              ('（'+s.get('display_context_note','')+'）' if s.get('display_context_note') else '') for s in profile['senses']),
            '代表读音：'+'；'.join('/'+p['text']+'/ ['+','.join(p['display_labels'])+'] '+p['pronunciation_scope']
                                 for p in profile['pronunciations'] if p['representative']),
            '待核：'+(profile['pronunciation_pending_reason'] or '')+f"；未裁定表达组 {len(profile['unresolved_meanings'])}", '']
    (out/'preview.md').write_text('\n'.join(lines),encoding='utf-8')
    return stats


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--database',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--limit',type=int)
    a=p.parse_args()
    print(json.dumps(preview(a.database,a.evidence,a.out,a.limit),ensure_ascii=False,indent=2))
