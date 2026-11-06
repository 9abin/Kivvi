"""One prespecified prompt revision, with identical retrieval in all three arms.

No labels or previous model answers enter inference. Baseline snapshots stay
read-only. Scoring uses the unchanged scorer only after all responses finish.
"""
import argparse
import gzip
import hashlib
import json
import shutil
import time
from pathlib import Path

import run_evaluation as baseline

HERE=Path(__file__).resolve().parent
VERSION='four-domain-explicit-state-compare-v2'
PROMPT=baseline.PROMPT.replace(
    '너는 회사 내부규정의 가상 개정 전후에 새로 생긴 문제를 점검한다.',
    '너는 회사 내부규정의 가상 개정 전후를 비교하여 새로 생긴 문제와 해소된 기존 문제를 모두 점검한다.',
).replace('[실제 입력 데이터]', '''전후 상태 비교 절차:
변경 조문마다 제공된 비교 후보와 같은 제도·대상·조건·속성을 규정하는지 먼저 확인한다.
관계가 같으면 before의 양쪽 원문으로 문제 유무를 판단한 다음, after의 양쪽 원문으로 다시 판단한다.
after가 일치한다는 이유만으로 바로 issues=[]로 끝내지 않는다. before가 불일치하고 after가 일치하면 resolved로 보고한다.
before가 일치하고 after가 불일치하면 new로 보고한다. 둘 다 일치하거나 같은 기존 문제가 전후 유지되면 보고하지 않는다.
제공된 각 관계를 독립적으로 확인한다. 원문에 확인되지 않은 조건·우선순위·외부 법령으로 불일치를 없애거나 만들지 않는다.
위 절차의 중간 사고 과정은 출력하지 않고, 확인된 결과와 원문 근거만 기존 JSON 형식으로 출력한다.

[실제 입력 데이터]''')

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--env',type=Path,default=HERE/'.env')
    ap.add_argument('--cache',type=Path,default=HERE/'.cache')
    ap.add_argument('--results',type=Path,default=HERE/'runs/state-compare-v2')
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--cases',nargs='*')
    ap.add_argument('--phase',choices=['all','judge','score'],default='all')
    args=ap.parse_args(); args.data=HERE; args.k=10
    if not args.results.resolve().is_relative_to((HERE/'runs').resolve()):
        raise ValueError('Choose a new path under runs/; published results are read-only.')
    args.results.mkdir(parents=True,exist_ok=True)
    for name,h in json.loads((HERE/'pre-run-freeze.json').read_text()).items():
        assert digest(HERE/name)==h,name
    source=HERE/'results/reviewed/retrieval.jsonl.gz'
    target=args.results/'retrieval.jsonl'
    if not target.exists():
        with gzip.open(source,'rb') as src, target.open('wb') as dst: shutil.copyfileobj(src,dst)
    if args.phase=='score': baseline.m.score(args); return
    api=baseline.m.OpenAIClient(args.env,args.cache)
    config={'prompt_version':VERSION,'prompt_sha256':hashlib.sha256(PROMPT.encode()).hexdigest(),
            'runner_sha256':digest(Path(__file__)),'model':api.model,'reasoning_effort':api.effort,
            'methods':baseline.METHODS,'k':10,'retrieval_sha256':digest(target),
            'baseline_retrieval_gzip_sha256':digest(source),'frozen_inputs':json.loads((HERE/'pre-run-freeze.json').read_text()),
            'development_set_reuse':True,'new_held_out_evaluation':False,
            'selection_policy':'One prespecified revision; apply to all three methods and all selected cases; no case-wise fallback or best-output selection.'}
    path=args.results/'config.json'
    if path.exists(): assert json.loads(path.read_text())==config,'Experiment changed; choose a fresh results directory.'
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.results/'prompt.txt').write_text(PROMPT,encoding='utf-8')
    # Filter retrieval only in memory; the source snapshot is never changed.
    baseline.PROMPT=PROMPT; baseline.PROMPT_VERSION=VERSION
    original_jl=baseline.m.jl
    if args.cases:
        def selected_jl(path):
            rows=original_jl(path)
            return [r for r in rows if r['id'] in args.cases] if path==target else rows
        baseline.m.jl=selected_jl
    try:
        baseline.judge(args,api)
    finally:
        (args.results/('api-usage-'+str(time.time_ns())+'.json')).write_text(json.dumps({'calls':api.calls,'usage':api.tokens},indent=2),encoding='utf-8')
        api.db.close()
    if args.phase=='all': baseline.m.score(args)

if __name__=='__main__': main()
