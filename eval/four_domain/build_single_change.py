"""Build one-operation fixtures; no inference and no changes to old snapshots."""
import collections,copy,hashlib,json,re,shutil
from pathlib import Path
import fixture_tools as ft
HERE=Path(__file__).resolve().parent
OUT=HERE/'single_change'

def article(s):return re.sub(r'-\d+-\d+-\d+$','',s)
def evidence(c,logical):
    for row in c.values():
        if row.get('logical_id',row['id'])==logical:
            return dict(logical_item_id=logical,article_id=row['id'],item_id=row['id'],document=row['document'],label=row['label'],quote=row['text'])
        for item in row.get('items',[]):
            if item['logical_id']==logical:
                return dict(logical_item_id=logical,article_id=row['id'],item_id=item['id'],document=row['document'],label=row['label'],quote=item['text'])
    raise AssertionError(logical)

def facts(c,f):
    if f['kind']=='broken_citation':
        groups=collections.defaultdict(list)
        for source in f['sources']:groups[article(source)].append(source)
        pairs=[(f['id']+':'+aid,aid,f['target']) for aid in groups]
    else:pairs=[(f['id'],f['left'],f['right'])]+f.get('extra_pairs',[])
    out={};issues=[]
    for key,left,right in pairs:
        le,ri=evidence(c,left),evidence(c,right)
        if f['kind']=='broken_citation':
            values=[]
            for source in groups[left]:
                op=next(op for op in f['right_ops'] if op.get('logical_item_id',op.get('logical_unit_id'))==source)
                quote=evidence(c,source)['quote']
                assert (op['old'] in quote)!=(op['new'] in quote)
                values.append(f['target'] if op['old'] in quote else f['left_ops'][0]['new_id'])
            assert len(set(values))==1
            lv,rv=values[0],ri['article_id'];mismatch=lv not in c
        else:
            lv=sorted(set(re.findall(f['pattern'],' '.join(le['quote'].split()))))
            rv=sorted(set(re.findall(f['pattern'],' '.join(ri['quote'].split()))))
            assert lv and rv,key
            mismatch=lv!=rv
        out[key]=dict(family=f['id'],domain=f['domain'],kind=f['kind'],property=f['id'],left=le,right=ri,left_value=lv,right_value=rv)
        if mismatch:issues.append(key)
    return out,sorted(issues)

def reverse(op):
    op=copy.deepcopy(op)
    if op['op']=='renumber':
        original_id=op['logical_article_id']
        op['old_label'],op['new_label']=op['new_label'],op['old_label'];op['new_id']=original_id
    else:op['old'],op['new']=op['new'],op['old']
    return op

def clean_op(base,f):
    # A real byte edit at one location, with no substantive policy change.
    # These are explicitly limited formatting controls, not policy-change tests.
    op=f['left_ops'][0];logical=op.get('logical_item_id') or op.get('logical_article_id')
    if logical in base:
        row=base[logical]
        if row.get('items'):logical=row['items'][0]['logical_id']
        else:
            text=row['text'];old=next(t for t in text.splitlines() if ' ' in t)
            assert text.count(old)==1
            return dict(op='replace_text',logical_unit_id=logical,old=old,new=old.replace(' ','  ',1))
    row,item=ft.locate_item(base,logical);text=item['text'];assert ' ' in text
    return dict(op='rewrite',logical_item_id=logical,expected_old=text,new=text.replace(' ','  ',1))

def describe(before,op):
    logical=op.get('logical_item_id') or op.get('logical_article_id') or op.get('logical_unit_id')
    e=evidence(before,logical)
    if op['op']=='renumber':change=f"{op['old_label']}를 {op['new_label']}로 이동한다. 내용은 유지하고 옛 번호는 비우며 별도 경과규정은 두지 않는다."
    else:change=f"다음 문구 한 곳을 교체한다.\n변경 전: {json.dumps(op.get('old',op.get('expected_old')),ensure_ascii=False)}\n변경 후: {json.dumps(op['new'],ensure_ascii=False)}"
    return f"{e['document']} {e['label']} ({logical})의 {change}\n이 한 곳 외에는 수정하지 않는다."

def main():
    OUT.mkdir(exist_ok=True)
    base={r['id']:r for r in ft.readjl(HERE/'base_corpus.jsonl')}
    families=ft.readjl(HERE/'authoring/families.jsonl')
    instruction=ft.readjl(HERE/'inputs.jsonl')[0]['instruction']
    inputs=[];gold=[];auth=[];questions=['# 한 문항·한 곳 변경: 60문항',''];review=['# 한 곳 변경 정답·전후 근거 — AI 검수 초안','']
    for f in sorted(families,key=lambda f:(f['domain'],f['id'])):
        assert len(f['left_ops'])==1
        edit=f['left_ops'][0]
        for kind in ['new','clean','resolved']:
            cid=f'SC{len(inputs)+1:03}'
            setup=[edit] if kind=='resolved' else []
            before=ft.apply_ops(base,setup)
            op=reverse(edit) if kind=='resolved' else clean_op(base,f) if kind=='clean' else copy.deepcopy(edit)
            after=ft.apply_ops(before,[op])
            bf,bi=facts(before,f);af,ai=facts(after,f)
            delta={'new':sorted(set(ai)-set(bi)),'resolved':sorted(set(bi)-set(ai))}
            assert (bool(delta[kind]) and not delta['new' if kind=='resolved' else 'resolved']) if kind!='clean' else not bi and not ai
            q=dict(id=cid,instruction=instruction,amendment=describe(before,op),amendment_ops=[op],base_corpus='base_corpus.jsonl',before_overlay=f'cases/{cid}/before_overlay.jsonl',after_overlay=f'cases/{cid}/after_overlay.jsonl')
            for state,corpus in [('before',before),('after',after)]:ft.writejl(OUT/q[state+'_overlay'],ft.overlay(base,corpus))
            inputs.append(q)
            gold.append(dict(id=cid,family=f['id'],domain=f['domain'],domains=[f['domain']],scope='single_operation',component_families=[f['id']],topic=f['title'],scenario_type=kind,expected_delta=delta,before_facts=bf,after_facts=af,before_issues=bi,after_issues=ai,target_issue_ids=list(af),label_status='AI_DRAFT_REQUIRES_HUMAN_REVIEW'))
            auth.append(dict(id=cid,setup_ops=setup,family=f['id'],domain=f['domain'],scenario_type=kind,condition=f['condition'],clean_control='formatting_only' if kind=='clean' else None))
            questions += [f'## {cid}','',instruction,'',q['amendment'],'']
            review += [f"## {cid} — {f['domain']} / {f['title']} / {kind}",'',q['amendment'],'',f"가상 시작 상태 준비: {json.dumps(setup,ensure_ascii=False)}",'',f"기대 변화: {json.dumps(delta,ensure_ascii=False)}",'',f['condition'],'']
            for key in af:
                review += [f'### {key}','']
                for state,fs in [('before',bf),('after',af)]:
                    review += [state+': '+json.dumps({k:fs[key][k] for k in ['left_value','right_value']},ensure_ascii=False)]
                    review += [f"- {fs[key][side]['article_id']}: {fs[key][side]['quote']}" for side in ['left','right']]
                review+=['']
    assert len(inputs)==60
    for name,rows in [('inputs.jsonl',inputs),('gold.jsonl',gold),('authoring/scenarios.jsonl',auth),('authoring/families.jsonl',families)]:ft.writejl(OUT/name,rows)
    for name in ['base_corpus.jsonl','score_results.py']:shutil.copyfile(HERE/name,OUT/name)
    (OUT/'questions.md').write_text('\n'.join(questions),encoding='utf-8')
    (OUT/'review.md').write_text('\n'.join(review),encoding='utf-8')
    frozen={name:ft.sha(OUT/name) for name in ['inputs.jsonl','gold.jsonl','base_corpus.jsonl','score_results.py','authoring/families.jsonl','authoring/scenarios.jsonl']}
    frozen.update({'../domain_ontology.reviewed.db':ft.sha(HERE/'domain_ontology.reviewed.db'),'../validated_graph.py':ft.sha(HERE/'validated_graph.py'),'../run_evaluation.py':ft.sha(HERE/'run_evaluation.py'),'../run_state_compare.py':ft.sha(HERE/'run_state_compare.py')})
    (OUT/'pre-run-freeze.json').write_text(json.dumps(frozen,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cases':60,'operations_per_case':1,'domains':dict(collections.Counter(g['domain'] for g in gold)),'types':dict(collections.Counter(g['scenario_type'] for g in gold)),'model_evaluation_performed':False},ensure_ascii=False))

if __name__=='__main__':main()
