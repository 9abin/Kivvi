"""API-free validation of the released 120 before/after fixtures."""
import collections,hashlib,importlib.util,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def jl(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
sp=importlib.util.spec_from_file_location('fixture120',ROOT/'fixture_tools.py'); ft=importlib.util.module_from_spec(sp); sp.loader.exec_module(ft)
def get(c,l):
    for r in c.values():
        if r.get('logical_id',r['id'])==l: return r,r
        for i in r.get('items',[]):
            if i['logical_id']==l: return r,i
    raise AssertionError('Missing logical source '+l)
def main():
    questions=jl(ROOT/'inputs.jsonl'); gold={g['id']:g for g in jl(ROOT/'gold.jsonl')}
    authored={r['id']:r for r in jl(ROOT/'authoring/scenarios.jsonl')}
    families=jl(ROOT/'authoring/families.jsonl'); base={r['id']:r for r in jl(ROOT/'base_corpus.jsonl')}
    assert len(questions)==len(gold)==len(authored)==120
    assert len({q['id'] for q in questions})==120
    assert len(families)==20 and collections.Counter(f['domain'] for f in families)==dict.fromkeys(['인사','보수','휴가','휴직'],5)
    assert len({r['reg_id'] for r in base.values()})==90 and len(base)==3244
    assert collections.Counter(g['scope'] for g in gold.values())=={'single':60,'composite':60}
    assert collections.Counter(g['scenario_type'] for g in gold.values())=={'new':40,'clean':40,'resolved':40}
    frozen=json.loads((ROOT/'pre-run-freeze.json').read_text())
    for filename,h in frozen.items(): assert hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()==h,filename
    source=jl(ROOT/'source-manifest.jsonl'); assert len(source)==90
    for r in source:
        assert hashlib.sha256((ROOT/r['extracted_text']).read_bytes()).hexdigest()==r['extracted_text_sha256']
    for q in questions:
        g=gold[q['id']]; a=authored[q['id']]
        assert not any(k in q for k in ['scenario_type','expected_delta','target_issue_ids','component_families','domains','scope','family'])
        before=ft.load_overlay(base,jl(ROOT/q['before_overlay'])); after=ft.load_overlay(base,jl(ROOT/q['after_overlay']))
        assert before==ft.apply_ops(base,a['setup_ops'])
        assert after==ft.apply_ops(before,q['amendment_ops'])
        if g['scope']=='composite': assert set(g['domains'])=={'인사','보수','휴가','휴직'} and len(g['component_families'])==4
        for state,c in [('before',before),('after',after)]:
            issue_keys=set()
            for key,fact in g[state+'_facts'].items():
                for side in ['left','right']:
                    e=fact[side]; row,item=get(c,e['logical_item_id'])
                    assert e['article_id']==row['id'] and e['quote']==item['text'],(q['id'],state,key,side)
                if fact['kind']=='broken_citation': mismatch=fact['left_value'] not in c
                else: mismatch=fact['left_value']!=fact['right_value']
                if mismatch: issue_keys.add(key)
            assert issue_keys==set(g[state+'_issues'])
        b=set(g['before_issues']); aissues=set(g['after_issues'])
        assert set(g['expected_delta']['new'])==aissues-b
        assert set(g['expected_delta']['resolved'])==b-aissues
        if g['scenario_type']=='clean': assert b==aissues
        else:
            assert g['expected_delta'][g['scenario_type']]
            assert not g['expected_delta']['resolved' if g['scenario_type']=='new' else 'new']
    report={'validated_cases':120,'operation_application':'passed','overlay_reconstruction':'passed','source_quote_exactness':'passed','state_delta':'passed','domain_and_type_balance':'passed','no_label_fields_in_inputs':'passed','frozen_hashes':'passed','90_raw_source_hashes':'passed','model_api_called':False}
    (ROOT/'dataset-validation-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__': main()
