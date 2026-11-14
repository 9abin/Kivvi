"""Verify complete inference and package a reviewable, secret-free snapshot."""
import argparse,gzip,hashlib,json,shutil,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.append(str(HERE.parent))
from fixture_tools import readjl

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,default=HERE/'runs/luna');args=ap.parse_args()
    run=args.run.resolve();dest=HERE/'results/luna'
    if not run.is_relative_to((HERE/'runs').resolve()):raise ValueError('Input must be a completed run under runs/.')
    if dest.exists():raise ValueError('Published snapshot already exists; do not overwrite it.')
    config=json.loads((run/'config.json').read_text(encoding='utf-8'))
    for name,expected in config['input_hashes'].items():assert sha(HERE/name)==expected,name
    assert config['runner_sha256']==sha(HERE/'run_evaluation.py')
    inputs=readjl(HERE/'inputs.jsonl');ids={q['id'] for q in inputs}
    assert len(ids)==60 and all(len(q['amendment_ops'])==1 for q in inputs)
    methods={'rag','pr_graph_rag','domain_graph_rag'}
    wanted={(cid,method) for cid in ids for method in methods}
    predictions=readjl(run/'predictions.jsonl');detail=readjl(run/'case_results.jsonl');retrieval=readjl(run/'retrieval.jsonl')
    assert len(predictions)==len(detail)==180
    assert {(r['id'],r['method']) for r in predictions}=={(r['id'],r['method']) for r in detail}==wanted
    assert not readjl(run/'failed_tasks.jsonl')
    assert {r['model'] for r in predictions}=={config['model']}
    assert {r['prompt_version'] for r in predictions}=={'single-change-60-state-compare-v1'}
    assert len(retrieval)==60 and {r['id'] for r in retrieval}==ids
    for r in retrieval:
        assert r['focus_count']==1
        for state in ['before','after']:
            for method in methods:
                view=r['states'][state]['visible'][method]
                assert len(view['anchors'])==1 and len(view['candidates'])==10
    summary=json.loads((run/'summary.json').read_text())
    assert {m['method'] for m in summary['metrics']}==methods
    assert all(m['cases']==60 for m in summary['metrics'])
    assert summary['scorer_sha256']==sha(HERE/'score_results.py')
    dest.mkdir(parents=True)
    for name in ['config.json','predictions.jsonl','case_results.jsonl','summary.json','unjudged.jsonl','failed_tasks.jsonl','report.md']:
        shutil.copyfile(run/name,dest/name)
    for name in ['format-validation-retries.jsonl','format-retry-api-usage.json']:
        if (run/name).exists():shutil.copyfile(run/name,dest/name)
    with (run/'retrieval.jsonl').open('rb') as src,(dest/'retrieval.jsonl.gz').open('wb') as dst:
        with gzip.GzipFile(filename='',mode='wb',fileobj=dst,mtime=0) as gz:shutil.copyfileobj(src,gz)
    usage=[json.loads(p.read_text()) for p in sorted(run.glob('api-usage-*.json'))]
    (dest/'api-usage.json').write_text(json.dumps(usage,indent=2)+'\n',encoding='utf-8')
    checks=dict(complete_cases=60,complete_responses=180,methods=3,one_edit_per_case=True,one_search_focus_per_case=True,
                final_candidates_per_method=10,frozen_data_db_and_scorer_verified=True,same_model_and_prompt_all_methods=True,
                gold_status='AI_DRAFT_REQUIRES_HUMAN_REVIEW',clean_controls='20 formatting-only controls; not substantive policy changes',
                retrieval_uncompressed_sha256=sha(run/'retrieval.jsonl'))
    (dest/'verification.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest={p.name:sha(p) for p in dest.iterdir() if p.is_file()}
    (dest/'snapshot-hashes.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'published_snapshot':str(dest),'cases':60,'responses':180,'checks':'passed'}))

if __name__=='__main__':main()
