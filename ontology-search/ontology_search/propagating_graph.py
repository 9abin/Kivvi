"""Bounded, source-evidence event propagation for provisional impact retrieval.

An impact gate here is a transparent deterministic heuristic, never a claim of
semantic contradiction or an LLM judgment. No labels or model APIs are read.
"""
import collections,dataclasses,hashlib,json,re

@dataclasses.dataclass(frozen=True)
class Limits:
    initial_hops:int=2
    max_depth:int=6
    max_nodes:int=300
    max_events:int=600
    max_states_per_node:int=4
    fanout:int=40
    max_edges:int=20000

def digest(payload):
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def walk_events(root,payload,neighbors,limits=Limits()):
    queue=collections.deque([(root,payload,0,[],True)])
    seen={(root,digest(payload))};states=collections.Counter({root:1})
    visited_nodes={root};scores={};traces=collections.defaultdict(list)
    stats=collections.Counter();stats['enqueued']=1
    while queue:
        node,event,depth,path,allowed=queue.popleft();stats['processed']+=1
        if depth>=limits.max_depth:
            stats['depth_stops']+=1;continue
        if depth>=limits.initial_hops and not allowed:
            stats['gate_stops']+=1;continue
        if depth>=limits.initial_hops:stats['extra_hop_expansions']+=1
        edges=sorted(neighbors(node,event,depth),key=lambda e:(-e['weight'],e['target'],e['kind']))
        if len(edges)>limits.fanout:stats['fanout_trimmed']+=len(edges)-limits.fanout
        for edge in edges[:limits.fanout]:
            stats['edges_examined']+=1
            if stats['edges_examined']>limits.max_edges:
                stats['edge_budget_stops']+=1;queue.clear();break
            target=edge['target']
            if target==root or target==node:
                stats['self_or_root_skips']+=1;continue
            if target not in scores and len(scores)>=limits.max_nodes-1:
                stats['candidate_budget_stops']+=1;continue
            next_event=edge.get('payload',event);key=(target,digest(next_event));hop=depth+1
            trace=path+[dict(source=node,target=target,relation=edge['kind'],hop=hop,
                             impact_gate=bool(edge['impact']),reason=edge['reason'],evidence=edge.get('evidence',''))]
            score=edge['weight']*(.78**(hop-1))
            if target not in scores or score>scores[target]:scores[target]=score
            if len(traces[target])<3:traces[target].append(trace)
            if key in seen:
                stats['duplicate_event_skips']+=1;continue
            if states[target]>=limits.max_states_per_node:
                stats['node_state_stops']+=1;continue
            if target not in visited_nodes and len(visited_nodes)>=limits.max_nodes:
                stats['node_budget_stops']+=1;continue
            if len(seen)>=limits.max_events:
                stats['event_budget_stops']+=1;continue
            seen.add(key);states[target]+=1;visited_nodes.add(target)
            queue.append((target,next_event,hop,trace,bool(edge['impact'])));stats['enqueued']+=1
    stats['visited_nodes']=len(visited_nodes);stats['candidate_nodes']=len(scores)
    return scores,dict(traces),dict(stats)

STOP={'다음','경우','관련','관한','따른','따라','해당','직원','회사','한다','하며','되는','있다','있는','대한','하여','규정','지침','제조','조항','개정','별도','사항','기준','범위'}
def terms(text):
    result=set()
    for word in re.findall(r'[가-힣]{2,}',text):
        for tail in ['에서는','으로는','에서','으로','에게','에는','하는','이란','이라','한다','하며','하여','되는','따라','한다','하고','하여야','의','은','는','을','를','이','가']:
            if word.endswith(tail) and len(word)-len(tail)>=2:word=word[:-len(tail)];break
        if len(word)>=2 and word not in STOP:result.add(word)
    return result

class PropagatingGraph:
    def __init__(self,corpus,schema,source_chunks,labelcode,norm):
        self.corpus=corpus;self.schema=schema;self.norm=norm
        self.by_reg=collections.defaultdict(list);self.ref_out=collections.defaultdict(list);self.ref_in=collections.defaultdict(list)
        self.doc_out=collections.defaultdict(set);self.doc_in=collections.defaultdict(list)
        self.article_edges=collections.defaultdict(list);self.tokens={};self.compact={};self.schema_cache={}
        names={r['document']:r['reg_id'] for r in corpus.values()}
        for aid,row in corpus.items():
            self.by_reg[row['reg_id']].append(aid);self.tokens[aid]=terms(row['text']);self.compact[aid]=norm(row['text'])
        for edge in schema.edges:
            row=corpus.get(edge['logical_article_id'])
            if row and edge['active'] and edge['logical_item_id'] in {i['logical_id'] for i in row['items']}:
                self.article_edges[row['id']].append(edge)
        for chunk in source_chunks:
            source=chunk['고유번호']+':'+labelcode(chunk['조항'])
            if source not in corpus:continue
            for cite in chunk.get('cites',[]):
                target=cite.get('대상') or ''
                if not cite.get('유형','').startswith('내부') or '_' not in target:continue
                rid,label=target.split('_',1);dest=rid+':'+labelcode(label)
                if dest not in corpus or source==dest:continue
                e=dict(source=source,target=dest,evidence=cite.get('원문',''))
                if e not in self.ref_out[source]:self.ref_out[source].append(e);self.ref_in[dest].append(e)
        # Whole-regulation delegation/reference is distinct from a specific cite.
        for aid,row in corpus.items():
            compact=self.compact[aid]
            for name,rid in names.items():
                if rid==row['reg_id']:continue
                if norm(name) in compact:
                    self.doc_out[aid].add(rid);self.doc_in[rid].append(aid)

    def initial_payload(self,anchor,seed_items,amendment,focus_text):
        scoped=[e for e in self.article_edges[anchor] if e['logical_item_id'] in set(seed_items)]
        concepts=collections.defaultdict(set)
        for e in scoped:concepts[e['concept']].update(e['attrs'])
        quoted=re.findall(r'이하\s*[“"‘\x27]([^”"’\x27]{1,20})[”"’\x27]',focus_text)
        return dict(change_hash=hashlib.sha256(amendment['개정문'].encode()).hexdigest(),
                    kind=amendment['종류'],root_reg=self.corpus[anchor]['reg_id'],
                    concepts={c:sorted(a) for c,a in concepts.items()},
                    scope_terms=sorted(terms(focus_text+' '+amendment['개정문'])),
                    definition_terms=sorted(set(quoted)) if amendment['종류']=='정의 변경' else [],
                    focus_text=focus_text)

    def neighbors(self,node,payload,depth):
        result=[];row=self.corpus[node];scope=set(payload['scope_terms']);historical=row['label'].startswith('부칙')
        def add(target,kind,weight,impact,reason,evidence='',event=None):
            if target in self.corpus and target!=node:
                result.append(dict(target=target,kind=kind,weight=weight,impact=impact,reason=reason,evidence=evidence,payload=event or payload))
        for e in self.ref_in[node]:
            historical_source=self.corpus[e['source']]['label'].startswith('부칙')
            if payload['kind']=='번호 이동' and historical_source:continue
            add(e['source'],'specific_reference_in',1.0,True,'변경된 기준을 번호로 직접 참조하므로 재검토가 필요하다.',e['evidence'])
        for e in self.ref_out[node]:
            if depth==0 and self.norm(e['evidence']) not in self.norm(payload['focus_text']):continue
            add(e['target'],'specific_reference_out',.90,True,'영향 조항이 명시적으로 인용하는 적용 근거다.',e['evidence'])
        for target in self.doc_in[row['reg_id']]:
            overlap=scope & self.tokens[target]
            add(target,'regulation_delegation_in',.74 if overlap else .40,bool(overlap),
                '규정 전체를 참조하며 같은 변경 주제어가 있다.' if overlap else '규정 전체 참조 후보이나 같은 대상·속성의 영향은 미확인.',row['document'])
        for rid in self.doc_out[node]:
            candidates=[]
            for target in self.by_reg[rid]:
                overlap=scope & self.tokens[target]
                if overlap:candidates.append((len(overlap),target))
            for count,target in sorted(candidates,key=lambda x:(-x[0],x[1]))[:12]:
                add(target,'regulation_delegation_out',.70,min(count,3)>=2,'준용 문서 안에서 변경 주제어가 겹치는 적용 기준 후보다.',self.corpus[target]['label'])
        for term in payload['definition_terms']:
            if row['reg_id']!=payload['root_reg']:continue
            for target in self.by_reg[payload['root_reg']]:
                if self.norm(term) in self.compact[target]:
                    add(target,'definition_use',1.0,True,'동일 규정 범위에서 변경된 정의의 용어를 사용한다.',term)
        # A shared intermediate article cannot introduce new concept/domain keys.
        current_edges=self.article_edges.get(node,[])
        cache_key=digest(payload['concepts'])
        if cache_key not in self.schema_cache:
            candidates=[]
            for target,edges in self.article_edges.items():
                matching={};matched_attrs=set()
                for concept,attrs in payload['concepts'].items():
                    for edge in edges:
                        if not self.schema.related(concept,edge['concept']):continue
                        overlap=set(attrs)&edge['attrs']
                        if attrs and not overlap:continue
                        matching[concept]=attrs;matched_attrs.update(overlap)
                if matching:candidates.append((target,matching,matched_attrs))
            self.schema_cache[cache_key]=candidates
        for target,matching,matched_attrs in self.schema_cache[cache_key]:
            if target==node:continue
            # Current node must carry this same original concept branch.
            if not any(self.schema.related(c,e['concept']) for c in matching for e in current_edges):continue
            overlap=scope & self.tokens[target]
            event={**payload,'concepts':matching}
            add(target,'schema_concept_attribute',.53+min(len(matched_attrs),3)*.035,bool(overlap and matched_attrs),
                '원래 개념·속성 가지와 변경 주제어가 겹친다. 실제 모순 확정은 아니다.',','.join(sorted(matched_attrs)),event)
        return result

    def rank(self,anchor,payload,lexical,limits=Limits()):
        scores,paths,stats=walk_events(anchor,payload,self.neighbors,limits)
        ranked=sorted(scores,key=lambda aid:(-scores[aid],-lexical.get(aid,0),aid))
        return ranked,paths,stats
