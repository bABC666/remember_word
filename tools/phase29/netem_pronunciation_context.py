"""Typed, position-bound pronunciation labels from fixed Wiktionary text.

Local accents replace enclosing geographical alternatives. Features refine them;
qualifiers bind to a phonetic target, including non-extracted IPAchar/enPR targets.
Raw label templates and their original offsets remain available for inspection.
"""
import re

from app.services.rich_dictionary_parser import HEADING, clean_links

LABEL_TEMPLATES={'a','accent','q','qualifier','sense'}
TARGETS={'IPA','IPAchar','enPR','audio','audio-IPA','rhymes','homophones','hmp'}
QUALIFIER=re.compile(r'(?i)weak|strong form|stressed|dated|obsolete|rare|uncommon|nonstandard|folk|dialect|archaic|historical|proscribed|deprecated|less common|minority|sometimes|some speakers|colloquial|before |after |prevocalic|preconsonantal|^final$|intervocalic|rapid speech|\b(noun|verb|adjective|adverb|past|present|plural|singular|participle)\b')
FEATURE=re.compile(r'(?i)raising|tensing|coalescen|dropping|flapping|glottali|vocali|rhotic|monophth|fronting|unglid|affrication|split|merger|smoothing|breaking|cot-caught|horse-hoarse|pane-pain|toe-tow|near-square|cheer-chair|square-nurse|fair-fur|pour-poor|wine-whine|pin-pen|doll-dole|father-bother|hurry-furry|cure-fir|fool-fall|show-sure|Mmmm')


def mask_annotations(text):
    def blank(match):
        return ''.join('\n' if c=='\n' else ' ' for c in match[0])
    text=re.sub(r'<!--.*?-->',blank,text,flags=re.DOTALL)
    return re.sub(r'<ref\b[^>]*(?:/>|>.*?</ref\s*>)',blank,text,flags=re.DOTALL|re.IGNORECASE)


def split_parameters(text):
    return re.split(r'\|(?![^\[]*\]\])',text)


def templates(line):
    return [(m.start(),m.end(),m[1],[arg.strip() for arg in split_parameters(m[2])]) for m in
            re.finditer(r'\{\{([^\s|{}]+)\|([^{}]*)\}\}',line)]


def labels(raw):
    # Split the source list outside links before decoding link display text.
    result=[]
    for token in re.split(r'[,|](?![^\[]*\]\])',raw):
        token=clean_links(token).strip()
        wrapped=re.findall(r'<<([^<>]+)>>',token)
        remainder=re.sub(r'<<[^<>]+>>','',token).strip()
        pieces=wrapped if wrapped and not remainder else [re.sub(r'<<([^<>]+)>>',r'\1',token).strip()]
        result.extend(piece for piece in pieces if piece and piece not in {'en','_'})
    return result


def empty_context():
    return {'accents':[],'features':[],'qualifiers':[],'bindings':[]}


def apply_labels(base,bindings):
    accents=[];features=[];qualifiers=[];scope_expressions=[];unresolved=[]
    for binding in bindings:
        values=labels(binding['raw_value'])
        if binding['axis']=='accent':
            for value in values:
                if re.search(r'(?i)\s+or\s+|\bexcluding\b|\bwithout\b',value):
                    accents.append(value);scope_expressions.append(value);unresolved.append(value)
                elif re.search(r'(?i)\s+with(?: the)?\s+',value):
                    place,condition=re.split(r'(?i)\s+with(?: the)?\s+',value,maxsplit=1)
                    accents.append(place);features.append(condition);scope_expressions.append(value)
                elif re.search(r'(?i)weak[- ]vowel',value):features.append(value)
                elif QUALIFIER.search(value):qualifiers.append(value)
                elif FEATURE.search(value):features.append(value)
                else:accents.append(value)
        else:qualifiers.extend(values)
    inherited_qualifiers=base['qualifiers']
    if any(re.search(r'(?i)(weak|strong) forms?|unstressed|^stressed$',v) for v in qualifiers):
        inherited_qualifiers=[v for v in inherited_qualifiers if not re.search(r'(?i)(weak|strong) forms?|unstressed|^stressed$',v)]
    def feature_axis(value):
        return re.sub(r'^(?:non-|no\s+)', '',value.lower())
    axes={feature_axis(value) for value in features}
    inherited_features=[v for v in base['features'] if feature_axis(v) not in axes]
    return {'accents':list(dict.fromkeys(accents if accents else base['accents'])),
            'features':list(dict.fromkeys(inherited_features+features)),
            'qualifiers':list(dict.fromkeys(inherited_qualifiers+qualifiers)),
            'scope_expressions':list(dict.fromkeys(base.get('scope_expressions',[])+scope_expressions)),
            'unresolved':list(dict.fromkeys(base.get('unresolved',[])+unresolved)),
            'bindings':base['bindings']+bindings}


def binding(raw_value,axis,number,span,kind,raw):
    return {'raw_value':raw_value,'axis':axis,'line':number,'template_span':list(span),
            'template_kind':kind,'raw_template':raw[span[0]:span[1]]}


def parameters(args,number,span,kind,raw,index=None):
    keys={'a':'accent','q':'qualifier'} if index is None else {f'a{index}':'accent',f'q{index}':'qualifier'}
    return [binding(arg.split('=',1)[1],keys[arg.split('=',1)[0]],number,span,kind,raw)
            for arg in args if '=' in arg and arg.split('=',1)[0] in keys]


def parse(text,word):
    result=[];english=False;in_pron=False;etymology='';header='';ancestors={}
    raw_lines=text.splitlines();masked=mask_annotations(text).splitlines()
    for number,(raw,line) in enumerate(zip(raw_lines,masked),1):
        heading=HEADING.fullmatch(line.strip())
        if heading:
            level,name=len(heading[1]),heading[2].strip()
            if level==2:english,etymology=name=='English',''
            if english and name.startswith('Etymology'):etymology=name
            in_pron=english and bool(re.fullmatch(r'Pronunciation(?: \d+)?',name))
            if in_pron:
                header=f'{etymology or "English"}{" / "+name if name!="Pronunciation" else ""}:L{number}'
                ancestors={}
            continue
        marker=re.match(r'^\s*([:*]+)',line)
        if not in_pron or not marker:continue
        depth=len(marker[1]);ancestors={d:v for d,v in ancestors.items() if d<depth}
        base=ancestors[max(ancestors)] if ancestors else empty_context()
        ts=templates(line)
        targets=[i for i,t in enumerate(ts) if t[2] in TARGETS]
        attached={i:[] for i in targets};standalone=[]
        for i,(start,end,kind,args) in enumerate(ts):
            if kind not in LABEL_TEMPLATES:continue
            arguments=[a for a in args if '=' not in a and a!='en']
            tag=binding('|'.join(arguments),'accent' if kind in {'a','accent'} else 'qualifier',number,(start,end),kind,raw)
            following=next((target for target in targets if target>i),None)
            previous=next((target for target in reversed(targets) if target<i),None)
            target=following if following is not None else previous
            if target is None:standalone.append(tag)
            else:attached[target].append(tag)
        plain=re.sub(r'\{\{.*?\}\}','',line[marker.end():]).strip(' :;,\'')
        structural=[]
        if re.fullmatch(r'(?i)(weak|strong) forms?',plain):
            structural=[binding(plain.lower(),'qualifier',number,(marker.end(),len(line)),'plain',raw)]
        if not targets:
            if standalone or structural:ancestors[depth]=apply_labels(base,standalone+structural)
            continue
        line_scope=base
        for target in targets:
            start,end,kind,args=ts[target]
            own=attached[target]+structural+parameters(args,number,(start,end),kind,raw)
            # enPR followed by IPA is a parallel transcription of the same item.
            previous=next((i for i in reversed(targets) if i<target),None)
            if kind=='IPA' and previous is not None and ts[previous][2]=='enPR':
                a,b,k,arguments=ts[previous]
                own=attached[previous]+parameters(arguments,number,(a,b),k,raw)+own
            context=apply_labels(line_scope,own)
            inheritable=kind in {'IPA','enPR'} or (kind in {'audio','audio-IPA'} and args and args[0]=='en')
            if inheritable and (len(targets)==1 or (len(targets)==2 and ts[targets[0]][2]=='enPR' and kind=='IPA')):
                ancestors[depth]=context
            if inheritable:
                # Geography and declared grammatical/form scope span parallel
                # transcriptions on a line; rare/dated/etc stay target-local.
                scoped=[v for v in context['qualifiers'] if any(b['template_kind']=='sense' and v in labels(b['raw_value']) for b in context['bindings'])
                        or re.fullmatch(r'(?i)noun|verb|adjective|adverb|past|present|plural|singular|participle|past participle|(weak|strong) forms?|(un)?stressed',v)]
                line_scope=dict(base,accents=context['accents'],qualifiers=list(dict.fromkeys(base['qualifiers']+scoped)),bindings=context['bindings'])
            if kind=='enPR':
                if len(targets)==1:ancestors[depth]=context
                continue
            if kind!='IPA' or not args or args[0]!='en':continue
            prefix=re.sub(r'\{\{.*?\}\}','',line[marker.end():start]).strip(' :;,\'')
            if re.search(r'[A-Za-z]{3}',prefix) and not re.fullmatch(r'(?i)(weak|strong) forms?',prefix):continue
            index=0
            for arg in args[1:]:
                chunks=list(re.finditer(r'/([^/\n]+)/|\[([^\[\]\n]+)\]',arg))
                if not chunks or re.sub(r'/[^/\n]+/|\[[^\[\]\n]+\]','',arg).strip(' ,;~'):continue
                index+=1
                item=apply_labels(context,parameters(args,number,(start,end),kind,raw,index))
                for chunk in chunks:
                    ipa=chunk[1] or chunk[2]
                    if not ipa or ipa.startswith('-') or ipa.endswith('-') or '...' in ipa or re.search(r'[{}<>|;=\n…]',ipa) or not re.search(r'[a-zæɑ-ʯ]',ipa):continue
                    display=list(dict.fromkeys(item['accents']+item['features']+item['qualifiers']+item.get('scope_expressions',[])))
                    result.append({'text':ipa,'notation':'phonemic' if chunk[1] else 'phonetic',
                    'language':'en','status':'source_verified','locator':f'enwiktionary:L{number}',
                    'raw_text':raw,'accent_labels':item['accents'],'feature_labels':item['features'],
                    'qualifier_labels':item['qualifiers'],'label_bindings':item['bindings'],
                    'display_labels':display,'scope_expressions':item.get('scope_expressions',[]),
                    'label_binding_pending':item.get('unresolved',[]),'raw_parameter':arg,
                    'pronunciation_scope':header,'template_span':[start,end],'source_word':word,
                    'quality_review':{'kind':'automated_full_word_pronunciation_context',
                        'core_confirmation':False,'note':'固定英语发音条目的原文及位置绑定；局部口音覆盖父级地域列表，限定语按 IPA/IPAchar/enPR 目标绑定。不声称人工确认。'}})
    return result
