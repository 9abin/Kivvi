"""Frozen-data, per-amended-article retrieval comparison; no gold in prepare/judge.

OpenAI calls use the user's existing Luna configuration. Three arms share focus
queries, round-robin aggregation, final k=10, excerpts, and existence metadata.
"""
import argparse,collections,concurrent.futures,hashlib,importlib.util,json,math,re,sys,threading,time,urllib.error
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
sys.path.insert(0,str(REPO/'src'))
from support import eval_common as m
from support.bm25 import grams
import validated_graph as ds
METHODS=['rag','pr_graph_rag','domain_graph_rag']
PROMPT_VERSION='four-domain-120-multifocus-v1'
PROMPT=m.PROMPT.replace('[실제 입력 데이터]', '''추가 공통 규칙:
한 개정안에 여러 제도가 함께 바뀔 수 있다. 각각의 문제를 before와 after에서 확인하고 발견한 새 문제·해소 문제를 모두 보고한다.
과거 개정 부칙에 당시 조 번호가 남아 있다는 이유만으로 현행 깨진 인용이라고 하지 않는다. 현재 계속 적용되는 본문·서식의 인용인지 구분한다.
같은 조항 쌍에서 여러 항·호가 같은 근거 조문을 인용하면 한 조항 쌍의 문제로 묶되 근거를 제시한다.
원문 중 [중략] 표시는 제공되지 않은 구간이다. 생략된 내용은 추정하거나 인용하지 않는다.

[실제 입력 데이터]''')

def changed_id(op): return op.get('logical_article_id') or op.get('logical_unit_id') or m.logical_article(op['logical_item_id'])
def focus_queries(q,before):
    result=[]
    for logical in sorted({changed_id(op) for op in q['amendment_ops']}):
        row=m.locate(before,logical); assert row,logical
        ops=[op for op in q['amendment_ops'] if changed_id(op)==logical]
        # The changed text comes first so a long contract cannot truncate it.
        changes='\n'.join((op.get('old','')+' → '+op.get('new','')) if op['op']!='renumber' else op['old_label']+' → '+op['new_label'] for op in ops)
        seed_ids={op.get('logical_item_id') for op in ops}
        source='\n'.join(i['text'] for i in row.get('items',[]) if i.get('logical_id') in seed_ids)
        if not source: source=row['text'][:700]
        text=(row['document']+' '+row['label']+' '+row['title']+'\n'+changes+'\n'+source)[:1200]
        result.append({'logical_id':logical,'ops':ops,'query':text})
    return result

def round_robin(rankings,limit=100):
    out=[]; seen=set()
    for rank in range(max(map(len,rankings),default=0)):
        for ranking in rankings:
            if rank<len(ranking) and ranking[rank] not in seen:
                seen.add(ranking[rank]); out.append(ranking[rank])
                if len(out)==limit: return out
    return out

TOKEN_CACHE={}
def cached_bm25(rows):
    # Exact same grams/BM25 equations; cache source tokenization only.
    bm=m.BM25.__new__(m.BM25); bm.k1=1.5; bm.b=.75; bm.tf=[]
    for r in rows:
        text=r['document']+' '+r['label']+' '+r['title']+'\n'+r['text']; h=m.digest(text)
        if h not in TOKEN_CACHE: TOKEN_CACHE[h]=collections.Counter(grams(text))
        bm.tf.append(TOKEN_CACHE[h])
    bm.len=np.array([sum(t.values()) for t in bm.tf],dtype=float); bm.avg=bm.len.mean()
    df=collections.Counter(); bm.inv=collections.defaultdict(list)
    for i,t in enumerate(bm.tf):
        df.update(t.keys())
        for g,f in t.items(): bm.inv[g].append((i,f))
    n=len(rows); bm.idf={g:math.log(1+(n-c+.5)/(c+.5)) for g,c in df.items()}
    return bm

def view(row,query,cap=3200):
    """Source-only excerpt selection, including unstructured long forms.

    Select original lines by lexical match, then restore source order. No labels
    or model answers enter selection. Every retained line is an original span.
    """
    text=row['text']
    if len(text)>cap:
        lines=text.splitlines(); words=set(re.findall(r'[가-힣]{2,}|\d+',query))
        ranked=sorted(range(len(lines)),key=lambda i:(-sum(w in lines[i] for w in words),i))
        keep=set(); length=0
        for i in ranked:
            size=len(lines[i])+10
            if length+size>cap: continue
            keep.add(i); length+=size
        chunks=[]; prior=-1
        for i in sorted(keep):
            if i!=prior+1: chunks.append('[중략]')
            chunks.append(lines[i]); prior=i
        text='\n'.join(chunks)
        if not text: text=row['text'][:cap]
    return {'article_id':row['id'],'document':row['document'],'label':row['label'],'title':row['title'],'text':text,'excerpted':text!=row['text']}

def freeze(args,api):
    expected=json.loads((HERE/'pre-run-freeze.json').read_text())
    for name,h in expected.items(): assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==h,name
    config={'provider':'openai','model':api.model,'embedding_model':api.embed_model,'dimensions':768,'reasoning_effort':api.effort,'methods':METHODS,'k':args.k,'prompt_version':PROMPT_VERSION,'prompt_sha256':hashlib.sha256(PROMPT.encode()).hexdigest(),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'schema_sha256':expected['validated_graph.py'],'scorer_sha256':expected['score_results.py'],'input_hashes':expected,'retrieval':'one query per actually amended logical article; per-focus RRF then round-robin unique aggregation','query_char_cap_per_focus':1200,'source_embedding_char_cap':1200,'context_char_cap_per_article':3200,'shared_auxiliary_metadata':'visible-source article/appendix reference existence from the same parser in all arms','max_parallel_api_calls':args.workers,'source_commit':(HERE/'UPSTREAM_COMMIT.txt').read_text().strip(),'reviewed_graph_database_sha256':hashlib.sha256(args.graph_db.read_bytes()).hexdigest(),'curated_edges':ds.DomainGraph(args.graph_db).stats['user_original_edges'],'no_previous_outputs_reused':False,'response_cache_policy':'identical full requests may reuse cached responses'}
    path=args.results/'config.json'
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        for key in ['model','embedding_model','k','prompt_sha256','runner_sha256','schema_sha256','input_hashes','reviewed_graph_database_sha256']: assert old[key]==config[key],key
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')

def prepare(args,api):
    base={r['id']:r for r in m.jl(HERE/'base_corpus.jsonl')}; queries=m.jl(HERE/'inputs.jsonl')
    if args.cases: queries=[q for q in queries if q['id'] in args.cases]
    states={}; texts={}; focuses={}
    for q in queries:
        for state in ['before','after']:
            c=m.snapshot(base,HERE/q[state+'_overlay']); states[q['id'],state]=c
            for r in c.values():
                t=m.embed_text(r); texts[m.digest(t)]=t
        focuses[q['id']]=focus_queries(q,states[q['id'],'before'])
    alltexts=list(texts.values()); print(json.dumps({'phase':'embedding','source_variants':len(alltexts),'focus_queries':sum(map(len,focuses.values()))}),flush=True)
    vv=api.embed(alltexts); vectors={m.digest(t):v for t,v in zip(alltexts,vv)}
    qt=list(dict.fromkeys(f['query'] for fs in focuses.values() for f in fs)); qv=api.embed(qt,task='RETRIEVAL_QUERY'); qvectors=dict(zip(qt,qv))
    records=[]; domain=ds.DomainGraph(args.graph_db)
    for q in queries:
        fs=focuses[q['id']]; logicals=[f['logical_id'] for f in fs]; querytext='\n'.join(f['query'] for f in fs)
        sr={}
        for state in ['before','after']:
            c=states[q['id'],state]; rows=list(c.values()); ids=[r['id'] for r in rows]
            anchors=[m.locate(c,l) for l in logicals]; excluded={r['id'] for r in anchors}
            bm=cached_bm25(rows); matrix=np.stack([vectors[m.digest(m.embed_text(r))] for r in rows])
            kg,edges=m.pr_graph(c,args.cache); norm={i:m.compact(r['text']) for i,r in c.items()}
            domain_edges=domain.edges
            per={method:[] for method in METHODS}; prpaths=collections.defaultdict(list); domainpaths=collections.defaultdict(list); trace=[]
            for f in fs:
                anchor=m.locate(c,f['logical_id']); before=m.locate(states[q['id'],'before'],f['logical_id']); after=m.locate(states[q['id'],'after'],f['logical_id'])
                lexarr=bm.scores(f['query']); lex=dict(zip(ids,lexarr.tolist())); dense=matrix@qvectors[f['query']]
                dr=[ids[i] for i in np.argsort(-dense) if ids[i] not in excluded][:200]
                br=[ids[i] for i in np.argsort(-lexarr) if ids[i] not in excluded][:200]
                rag=m.fuse(dr,br)
                cq=m.ChangeQuery(q['id']+':'+f['logical_id'],anchor['reg_id'],m.classify_ops({'amendment_ops':f['ops']}),starts=[anchor['id']])
                candidates=m.collect(kg,cq,lambda term:[i for i,t in norm.items() if m.compact(term) in t],lambda query,k:[],{'title':anchor['title'],'old':before['text'][:700],'new':after['text'][:700],'amend':f['query']})
                pg=[x.id for x in candidates if x.id in c and x.id not in excluded]
                seed=[o['logical_item_id'] for o in f['ops'] if o.get('logical_item_id')]
                dg,dp=domain.rank(c,[anchor],f['query'],lex,seed); dg=[i for i in dg if i not in excluded]
                ranks={'rag':rag,'pr_graph_rag':m.fuse(rag,pg),'domain_graph_rag':m.fuse(rag,dg)}
                for method,ranking in ranks.items(): per[method].append(ranking)
                for x in candidates:
                    for path in x.paths:
                        if path not in prpaths[x.id]: prpaths[x.id].append(path)
                for aid,paths in dp.items(): domainpaths[aid].extend(paths)
                trace.append({'logical_id':f['logical_id'],'query':f['query'],'rankings':{k:v[:30] for k,v in ranks.items()}})
            rankings={method:round_robin(lists) for method,lists in per.items()}; visible={}
            anchor_views=[view(r,next(f['query'] for f in fs if f['logical_id']==r.get('logical_id',r['id']))) for r in anchors]
            for method,ranking in rankings.items():
                chosen=ranking[:args.k]; available=excluded|set(chosen)
                cf=[{k:e[k] for k in ['src','raw','target_doc','target_reg','target_id','status','note']} for e in edges if m.logical_article(e['src']) in available and e['target_type'] in ['article','appendix']]
                visible[method]={'anchors':anchor_views,'candidates':[view(c[i],querytext) for i in chosen],'citation_facts':cf}
            sr[state]={'rankings':rankings,'visible':visible,'article_logical_ids':{r['id']:r.get('logical_id',r['id']) for r in rows},'pr_graph_paths':dict(prpaths),'domain_graph_paths':dict(domainpaths),'focus_retrieval':trace,'graph_stats':{'nodes':len(kg.nodes),'edges':len(kg.rows),'domain_edges':len(domain_edges)}}
        records.append({'id':q['id'],'instruction':q['instruction'],'amendment':q['amendment'],'retrieval_query':querytext,'focus_count':len(fs),'states':sr})
        m.writejl(args.results/'retrieval.jsonl',records)
        print(json.dumps({'phase':'retrieval','case':q['id'],'completed':len(records),'total':len(queries),'focus_count':len(fs)}),flush=True)

def judge(args,api):
    records=m.jl(args.results/'retrieval.jsonl'); path=args.results/'predictions.jsonl'
    predictions=m.jl(path) if path.exists() else []; done={(p['id'],p['method']) for p in predictions}
    locks={}; guard=threading.Lock(); failures=[]
    def infer(record,method):
        inp={'instruction':record['instruction'],'amendment':record['amendment'],'before':record['states']['before']['visible'][method],'after':record['states']['after']['visible'][method]}
        prompt=PROMPT+json.dumps(inp,ensure_ascii=False); key=m.digest(prompt)
        with guard: lock=locks.setdefault(key,threading.Lock())
        with lock:
            for attempt in range(5):
                try: result,info=api.generate(prompt); break
                except (urllib.error.URLError,ConnectionError,TimeoutError):
                    if attempt==4: raise
                    time.sleep(min(10,2*(attempt+1)))
        assert isinstance(result,dict) and isinstance(result.get('issues'),list)
        for issue in result['issues']:
            assert issue['status'] in ['new','resolved','persisting'] and issue['kind'] in ['value_mismatch','obligation_mismatch','broken_citation']
            assert all(k in issue for k in ['left_article_id','right_article_id','summary'])
        return {'id':record['id'],'method':method,'result':result,'api':info,'prompt_version':PROMPT_VERSION,'model':api.model}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks={pool.submit(infer,r,method):(r['id'],method) for r in records for method in METHODS if (r['id'],method) not in done}
        for task in concurrent.futures.as_completed(tasks):
            cid,method=tasks[task]
            try: p=task.result()
            except Exception as e:
                failures.append({'id':cid,'method':method,'error_type':type(e).__name__})
                print(json.dumps({'phase':'judge_error',**failures[-1]}),flush=True); continue
            predictions.append(p); m.writejl(path,sorted(predictions,key=lambda r:(r['id'],r['method'])))
            print(json.dumps({'phase':'judge','id':cid,'method':method,'completed':len(predictions),'expected':len(records)*3}),flush=True)
    m.writejl(args.results/'failed_tasks.jsonl',failures)
    if failures: raise RuntimeError('Some API tasks failed; resume --phase judge. Successful responses preserved.')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--phase',choices=['prepare','judge','score','all'],default='all')
    ap.add_argument('--cache',type=Path,default=HERE/'.cache')
    ap.add_argument('--results',type=Path,default=HERE/'runs/luna-reviewed'); ap.add_argument('--k',type=int,default=10)
    ap.add_argument('--workers',type=int,default=4); ap.add_argument('--cases',nargs='*')
    ap.add_argument('--env',type=Path,default=HERE/'.env'); ap.add_argument('--graph-db',type=Path,default=HERE/'domain_ontology.reviewed.db'); args=ap.parse_args()
    args.data=HERE
    if args.results.resolve().is_relative_to((HERE/'results').resolve()): raise ValueError('Published results are read-only; choose runs/.')
    args.results.mkdir(parents=True,exist_ok=True)
    if args.phase=='score': m.score(args); return
    api=m.OpenAIClient(args.env,args.cache); freeze(args,api)
    try:
        if args.phase in ['prepare','all']: prepare(args,api)
        if args.phase in ['judge','all']: judge(args,api)
    finally:
        (args.results/('api-usage-'+str(time.time_ns())+'.json')).write_text(json.dumps({'calls':api.calls,'usage':api.tokens,'phase':args.phase},indent=2),encoding='utf-8')
    if args.phase=='all': m.score(args)
if __name__=='__main__': main()
