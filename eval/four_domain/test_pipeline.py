import importlib.util,json
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('pipeline120',HERE/'run_evaluation.py'); p=importlib.util.module_from_spec(sp); sp.loader.exec_module(p)

def test_bm25_cache_exactly_matches_original_scores():
    rows=[{'document':'취업규칙','label':'제1조','title':'육아휴직','text':'육아휴직 기간은 3년 이내로 한다.'},{'document':'급여규정','label':'제2조','title':'보수','text':'기본급의 1/2 및 자녀수당을 지급한다.'}]
    docs=[r['document']+' '+r['label']+' '+r['title']+'\n'+r['text'] for r in rows]
    for query in ['육아휴직 4년','기본급의 2/3','없는 단어']:
        assert np.allclose(p.cached_bm25(rows).scores(query),p.m.BM25(docs).scores(query),rtol=0,atol=0)

def test_last_change_is_not_lost_to_global_query_truncation():
    qs=p.m.jl(HERE/'inputs-composite.jsonl'); q=max(qs,key=lambda q:len(q['amendment_ops']))
    base={r['id']:r for r in p.m.jl(HERE/'base_corpus.jsonl')}
    before=p.m.snapshot(base,HERE/q['before_overlay']); fs=p.focus_queries(q,before)
    expected={p.changed_id(op) for op in q['amendment_ops']}
    assert {f['logical_id'] for f in fs}==expected
    assert len(fs)>=4 and all(0<len(f['query'])<=1200 for f in fs)
    for f in fs:
        assert any((op.get('old') or op.get('old_label')) in f['query'] for op in f['ops'])

def test_round_robin_preserves_multiple_focuses_and_single_total_budget():
    assert p.round_robin([['a','x','y'],['b','x','z'],['c','x','w']],5)==['a','b','c','x','y']
    assert len(p.round_robin([['a'],['a'],['b']],10))==2

def test_long_forms_keep_relevant_tail_without_inventing_source_lines():
    lines=['앞쪽 반복 문장 '+str(i) for i in range(400)]+['매월 25일에 월급여를 지급한다.']
    row={'id':'x','document':'계약서','label':'별지','title':'계약서','text':'\n'.join(lines)}
    out=p.view(row,'매월 25일 월급여 지급',3200)
    assert len(out['text'])<=3200 and '매월 25일에 월급여를 지급한다.' in out['text']
    assert all(line=='[중략]' or line in lines for line in out['text'].splitlines())

def test_original_dataset_and_schema_frozen():
    import hashlib
    for name,h in json.loads((HERE/'pre-run-freeze.json').read_text()).items():
        assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==h

def test_prompt_has_same_schema_and_no_answer_hints():
    assert 'value_mismatch|obligation_mismatch|broken_citation' in p.PROMPT
    assert 'new|resolved' in p.PROMPT and '과거 개정 부칙' in p.PROMPT
    assert '정답 목록' not in p.PROMPT

def test_prompt_identity_with_recorded_run():
    import hashlib
    config=json.loads((HERE/'results/reviewed/config.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(p.PROMPT.encode()).hexdigest()==config['prompt_sha256']
    assert p.ds.DomainGraph().stats['user_original_edges']==151

def test_real_case_preparation_without_api(tmp_path):
    import argparse,hashlib
    class LocalVectors:
        def embed(self,texts,**kwargs):
            vectors=np.array([[b+1 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts],dtype=float)
            return vectors/np.linalg.norm(vectors,axis=1,keepdims=True)
    p.prepare(argparse.Namespace(data=HERE,cases=['Q029'],cache=tmp_path,results=tmp_path,k=10,graph_db=HERE/'domain_ontology.reviewed.db'),LocalVectors())
    records=p.m.jl(tmp_path/'retrieval.jsonl'); assert len(records)==1
    for state in ['before','after']:
        rs=records[0]['states'][state]
        for method in p.METHODS:
            view=rs['visible'][method]
            assert len(view['candidates'])==10
            assert {x['article_id'] for x in view['anchors']}.isdisjoint(x['article_id'] for x in view['candidates'])
        assert any(path['origin']=='user_original' for paths in rs['domain_graph_paths'].values() for path in paths)
