"""Verify one actual edit and one relation family per case, without API calls."""
import collections,hashlib,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import fixture_tools as ft
from build_single_change import facts,evidence

def main():
    qs=ft.readjl(HERE/'inputs.jsonl');gs={g['id']:g for g in ft.readjl(HERE/'gold.jsonl')}
    authored={a['id']:a for a in ft.readjl(HERE/'authoring/scenarios.jsonl')}
    families={f['id']:f for f in ft.readjl(HERE/'authoring/families.jsonl')}
    base={r['id']:r for r in ft.readjl(HERE/'base_corpus.jsonl')}
    assert len(qs)==len(gs)==len(authored)==60 and len({q['id'] for q in qs})==60
    assert len({r['reg_id'] for r in base.values()})==90
    assert collections.Counter(g['domain'] for g in gs.values())==dict.fromkeys(['인사','보수','휴가','휴직'],15)
    assert collections.Counter(g['scenario_type'] for g in gs.values())==dict.fromkeys(['new','clean','resolved'],20)
    for domain in ['인사','보수','휴가','휴직']:
        assert collections.Counter(g['scenario_type'] for g in gs.values() if g['domain']==domain)==dict.fromkeys(['new','clean','resolved'],5)
    for name,h in json.loads((HERE/'pre-run-freeze.json').read_text()).items():assert ft.sha(HERE/name)==h,name
    semantic_clean=[]
    for q in qs:
        g=gs[q['id']];a=authored[q['id']];ops=q['amendment_ops'];assert len(ops)==1
        assert len(g['component_families'])==len(g['domains'])==1 and g['scope']=='single_operation'
        assert not set(q)&{'scenario_type','expected_delta','family','domain','domains','component_families'}
        before=ft.load_overlay(base,ft.readjl(HERE/q['before_overlay']))
        after=ft.load_overlay(base,ft.readjl(HERE/q['after_overlay']))
        assert before==ft.apply_ops(base,a['setup_ops']) and after==ft.apply_ops(before,ops)
        bm={r.get('logical_id',r['id']):r for r in before.values()};am={r.get('logical_id',r['id']):r for r in after.values()}
        assert set(bm)==set(am)
        changed=[k for k in bm if bm[k]!=am[k]]
        assert len(changed)==1,(q['id'],changed)
        op=ops[0]
        if op['op']=='rewrite':assert op['expected_old']!=op['new']
        elif op['op'] in ['replace','replace_text']:assert op['old']!=op['new']
        else:assert op['old_label']!=op['new_label']
        for state,corpus in [('before',before),('after',after)]:
            fs,issues=facts(corpus,families[g['family']]);assert fs==g[state+'_facts'] and issues==g[state+'_issues']
            for fact in fs.values():
                for side in ['left','right']:
                    assert evidence(corpus,fact[side]['logical_item_id'])==fact[side]
        bi=set(g['before_issues']);ai=set(g['after_issues'])
        assert g['expected_delta']=={'new':sorted(ai-bi),'resolved':sorted(bi-ai)}
        if g['scenario_type']=='clean':
            assert not bi and not ai
            assert ''.join(bm[changed[0]]['text'].split())==''.join(am[changed[0]]['text'].split())
            semantic_clean.append(q['id'])
        else:
            assert g['expected_delta'][g['scenario_type']]
            assert len(a['setup_ops'])==(g['scenario_type']=='resolved')
    report=dict(validated_cases=60,operations_per_case=1,changed_logical_articles_per_case=1,relation_families_per_case=1,
                source_documents=90,source_quotes_exact=True,state_delta_verified=True,no_labels_in_model_inputs=True,
                balanced_domains_and_types=True,formatting_only_clean_controls=semantic_clean,
                model_evaluation_performed=False,gold_status='AI_DRAFT_REQUIRES_HUMAN_REVIEW')
    (HERE/'validation-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
