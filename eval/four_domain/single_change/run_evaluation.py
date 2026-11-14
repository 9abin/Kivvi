"""Evaluate one-edit cases using the unchanged three retrieval implementations."""
import argparse,hashlib,json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import run_evaluation as pipeline
from run_state_compare import PROMPT

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--phase',choices=['prepare','judge','score','all'],default='all')
    ap.add_argument('--env',type=Path,default=HERE.parent/'.env')
    ap.add_argument('--cache',type=Path,default=HERE.parent/'.cache')
    ap.add_argument('--results',type=Path,default=HERE/'runs/luna')
    ap.add_argument('--workers',type=int,default=4);ap.add_argument('--cases',nargs='*')
    args=ap.parse_args();args.data=HERE;args.k=10;args.graph_db=HERE.parent/'domain_ontology.reviewed.db'
    if not args.results.resolve().is_relative_to((HERE/'runs').resolve()):raise ValueError('Use single_change/runs/.')
    args.results.mkdir(parents=True,exist_ok=True)
    frozen=json.loads((HERE/'pre-run-freeze.json').read_text())
    for name,h in frozen.items():assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==h,name
    assert all(len(q['amendment_ops'])==1 for q in pipeline.m.jl(HERE/'inputs.jsonl'))
    if args.phase=='score':pipeline.m.score(args);return
    api=pipeline.m.OpenAIClient(args.env,args.cache)
    config=dict(dataset='single-change-60-v1',model=api.model,embedding_model=api.embed_model,reasoning_effort=api.effort,
                methods=pipeline.METHODS,k=10,input_hashes=frozen,prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
                runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),cases=sorted(args.cases) if args.cases else None)
    path=args.results/'config.json'
    if path.exists():assert json.loads(path.read_text())==config,'Run configuration changed; use a fresh directory.'
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    pipeline.HERE=HERE;pipeline.PROMPT=PROMPT;pipeline.PROMPT_VERSION='single-change-60-state-compare-v1'
    try:
        if args.phase in ['prepare','all']:pipeline.prepare(args,api)
        if args.phase in ['judge','all']:pipeline.judge(args,api)
    finally:
        (args.results/('api-usage-'+str(time.time_ns())+'.json')).write_text(json.dumps({'calls':api.calls,'usage':api.tokens},indent=2),encoding='utf-8')
        api.db.close()
    if args.phase=='all':pipeline.m.score(args)

if __name__=='__main__':main()
