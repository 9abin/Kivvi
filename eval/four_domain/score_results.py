"""Gold is opened only here, after retrieval and judging have completed."""
import collections
import hashlib
import json
import re

METHODS=['rag','pr_graph_rag','domain_graph_rag']
def jl(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]
def writejl(p,rows): p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
def article(s): return re.sub(r'-\d+-\d+-\d+$','',s)
def compact(s): return re.sub(r'\s+','',s or '')

def score_run(args):
    gold={g['id']:g for g in jl(args.data/'gold.jsonl')}
    retrieval={r['id']:r for r in jl(args.results/'retrieval.jsonl')}
    pred={(p['id'],p['method']):p for p in jl(args.results/'predictions.jsonl')}
    detail=[]; unjudged=[]; metrics=[]
    case_ids=[i for i in sorted(retrieval) if not args.cases or i in args.cases]
    for method in METHODS:
        counts=collections.Counter(); correct_by_type=collections.Counter()
        for cid in case_ids:
            g=gold[cid]; r=retrieval[cid]; output=pred[cid,method]['result']
            aliases={}
            for state in ['before','after']: aliases.update(r['states'][state]['article_logical_ids'])
            known={}
            for key,fact in g['after_facts'].items():
                pair=tuple(sorted(article(fact[s]['logical_item_id']) for s in ['left','right']))
                match=(fact['kind'],pair)
                assert match not in known or known[match]==key,'Ambiguous gold identity'
                known[match]=key
                for accepted_kind in fact.get("accepted_kinds",[]):
                    known[(accepted_kind,pair)]=key
            judged_pairs={p for _,p in known}
            actual={'new':set(),'resolved':set()}; unsupported_status=set(); findings=[]; pending_review=False
            for issue in output['issues']:
                pair=tuple(sorted(aliases.get(article(issue[s]),article(issue[s])) for s in ['left_article_id','right_article_id']))
                key=known.get((issue['kind'],pair))
                if key is None and pair in judged_pairs:
                    key='wrong_kind:'+issue['kind']+':'+','.join(pair)
                if key is None:
                    pending_review=True
                    unjudged.append({'id':cid,'method':method,'issue':issue,'review_status':'PENDING_HUMAN_REVIEW'})
                elif issue['status'] in actual: actual[issue['status']].add(key)
                else: unsupported_status.add(key)
                validity=[]
                for state,field in [('before','before_evidence'),('after','after_evidence')]:
                    vis=r['states'][state]['visible'][method]
                    texts={x['article_id']:x['text'] for x in vis['anchors']+vis['candidates']}
                    for e in issue.get(field,[]):
                        ok=bool(e.get('quote')) and compact(e['quote']) in compact(texts.get(e.get('article_id'),''))
                        counts['quotes']+=1; counts['valid_quotes']+=ok; validity.append(ok)
                findings.append({'matched_issue_id':key,'issue':issue,'all_provided_quotes_valid':bool(validity) and all(validity)})
            expected={s:set(g['expected_delta'][s]) for s in actual}
            known_correct=not unsupported_status and all(actual[s]==expected[s] for s in actual)
            correct=None if pending_review and known_correct else known_correct
            counts['cases']+=1; counts['case_correct']+=correct is True
            counts['case_pending_review']+=correct is None
            correct_by_type[g['scenario_type']]+=correct is True
            for status in actual:
                counts[status+'_tp']+=len(actual[status]&expected[status])
                counts[status+'_fp_judged']+=len(actual[status]-expected[status])
                counts[status+'_fn']+=len(expected[status]-actual[status])
            if g['scenario_type']=='clean': counts['clean_false_alarm']+=bool(actual['new'] or actual['resolved'] or unsupported_status)
            target_pairs=[tuple(sorted(article(g['after_facts'][k][s]['logical_item_id']) for s in ['left','right'])) for k in g['target_issue_ids']]
            searches={}
            for state in ['before','after']:
                rs=r['states'][state]; vis=rs['visible'][method]; mapping=rs['article_logical_ids']
                anchor={mapping[x['article_id']] for x in vis['anchors']}
                wanted=set().union(*(set(p) for p in target_pairs))-anchor
                ranked={mapping[i] for i in rs['rankings'][method][:args.k]}
                hit=len(wanted&ranked)
                counts['search_hit']+=hit; counts['search_total']+=len(wanted)
                searches[state]={'wanted':sorted(wanted),'hit':hit,'total':len(wanted)}
            detail.append({'id':cid,'method':method,'topic':g['topic'],'scenario_type':g['scenario_type'],'case_correct':correct,'known_gold_correct':known_correct,'pending_additional_findings_review':pending_review,'expected':{s:sorted(v) for s,v in expected.items()},'predicted_judged':{s:sorted(v) for s,v in actual.items()},'search':searches,'findings':findings})
        metrics.append({'method':method,**dict(counts),'case_accuracy':counts['case_correct']/counts['cases'] if counts['cases'] and not counts['case_pending_review'] else None,'correct_by_type':dict(correct_by_type),'unjudged_findings':sum(x['method']==method for x in unjudged)})
    writejl(args.results/'case_results.jsonl',detail); writejl(args.results/'unjudged.jsonl',unjudged)
    summary={'metrics':metrics,'model_evaluation_performed':True,'scope':'AI draft controlled issue list; additional findings require review; no completeness claim','scorer_sha256':hashlib.sha256((args.data/'score_results.py').read_bytes()).hexdigest()}
    (args.results/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    report=['# 전체 문서 개정 평가 결과','', '정답은 사람 검수 전 AI 초안입니다. 범위 밖 발견은 별도 검토합니다.','',
            '| 방식 | 새 문제 발생 문항 | 문제 없는 변경 문항 | 기존 문제 해소 문항 | 전체 문항 | 상대 조항 검색 |','|---|---:|---:|---:|---:|---:|']
    type_counts=collections.Counter(gold[i]['scenario_type'] for i in case_ids)
    for m in metrics:
        report.append('| '+m['method']+' | '+' | '.join(f"{m['correct_by_type'].get(s,0)}/{type_counts[s]}" for s in ['new','clean','resolved'])+f" | {m['case_correct']}/{m['cases']} (검토대기 {m['case_pending_review']}) | {m['search_hit']}/{m['search_total']} |")
    report+=['','동일 문서의 번호 이동으로 생긴 복수 인용 오류는 항목별로 채점하며, 문항 정답은 그 문항의 알려진 새 문제·해소 문제를 모두 맞혔을 때만 인정합니다.','']
    (args.results/'report.md').write_text('\n'.join(report),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
